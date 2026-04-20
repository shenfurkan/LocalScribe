from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QTextBrowser, QFileDialog,
    QPushButton, QLabel, QProgressBar, QSlider
)
from PySide6.QtCore import Signal, Qt, QUrl, QProcess
from PySide6.QtGui import QTextCursor
import tempfile

from ui.widgets.timestamp_highlighter import TimestampHighlighter
from ui.dialogs.export_dialog import ExportDialog
from ui.dialogs.rename_dialog import RenameDialog
from core.exporter import export_txt, _readable_time
from core.storage import StorageManager
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
import os
import html


class TranscriptPage(QWidget):
    transcript_deleted = Signal(str)    # transcript_id
    transcript_renamed = Signal()

    def __init__(self, storage: StorageManager):
        super().__init__()
        self.storage = storage
        self._transcript: dict | None = None
        self._current_segments = []
        self._show_timestamps = True
        self._editing = False
        self._streaming = False          # True while live transcription runs
        self._temp_audio_path: str | None = None  # ffmpeg-extracted temp WAV
        self._play_until_ms: int | None = None  # auto-pause target (segment-click playback)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Left: editor area ─────────────────────────────────────────
        editor_area = QWidget()
        editor_layout = QVBoxLayout(editor_area)
        editor_layout.setContentsMargins(32, 24, 16, 24)
        editor_layout.setSpacing(6)

        self.file_name_label = QLabel()
        self.file_name_label.setObjectName("TranscriptTitle")

        self.meta_label = QLabel()
        self.meta_label.setObjectName("TranscriptMeta")

        # Live progress bar (hidden when not streaming)
        self.live_progress = QProgressBar()
        self.live_progress.setRange(0, 0)
        self.live_progress.setFixedHeight(6)
        self.live_progress.hide()

        self.editor = QTextBrowser()
        self.editor.setObjectName("TranscriptEditor")
        self.editor.setReadOnly(True)
        self.editor.setOpenLinks(False)
        self.editor.anchorClicked.connect(self._seek_to_anchor)

        # Attach syntax highlighter — colours [HH:MM:SS] tokens violet inside plain text
        self.highlighter = TimestampHighlighter(self.editor.document())

        # ── Audio Player Setup ─────────────────────────────────────────
        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        
        self.audio_widget = QWidget()
        audio_layout = QHBoxLayout(self.audio_widget)
        audio_layout.setContentsMargins(0, 0, 0, 8)
        
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setObjectName("ActionBtn")
        self.play_btn.setFixedWidth(80)
        self.play_btn.clicked.connect(self._toggle_playback)
        
        self.audio_slider = QSlider(Qt.Horizontal)
        self.audio_slider.sliderMoved.connect(self.player.setPosition)
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("CardMeta")
        
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.durationChanged.connect(self.audio_slider.setMaximum)
        self.player.playingChanged.connect(self._on_playing_changed)
        self.player.errorOccurred.connect(self._on_player_error)
        self._ffmpeg_proc: QProcess | None = None
        self._pending_source_path: str | None = None  # original path for ffmpeg fallback

        audio_layout.addWidget(self.play_btn)
        audio_layout.addWidget(self.audio_slider)
        audio_layout.addWidget(self.time_label)
        self.audio_widget.hide()

        header_layout = QHBoxLayout()
        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(2)
        header_text_layout.addWidget(self.file_name_label)
        header_text_layout.addWidget(self.meta_label)

        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()

        editor_layout.addLayout(header_layout)
        editor_layout.addWidget(self.audio_widget)
        editor_layout.addWidget(self.live_progress)
        editor_layout.addWidget(self.editor, stretch=1)

        # ── Right: action panel ───────────────────────────────────────
        action_panel = self._build_action_panel()

        main_layout.addWidget(editor_area, stretch=1)
        main_layout.addWidget(action_panel)

    # ─────────────────────────────────────────────────────────────────
    # Action panel builder
    # ─────────────────────────────────────────────────────────────────

    def _build_action_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("ActionPanel")
        panel.setFixedWidth(220)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 24, 12, 24)
        layout.setSpacing(8)

        def section(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("ActionSectionLabel")
            return lbl

        # ── EXPORT ────────────────────────────────────────────────────
        layout.addWidget(section("EXPORT"))

        dl_btn = QPushButton("\U0001f4e5  Download\u2026")
        dl_btn.setObjectName("ActionBtn")
        dl_btn.clicked.connect(self._quick_download)

        srt_btn = QPushButton("\U0001f39e\ufe0f  Download SRT\u2026")
        srt_btn.setObjectName("ActionBtn")
        srt_btn.clicked.connect(self._open_srt_dialog)

        adv_btn = QPushButton("\u26a1  Advanced Export")
        adv_btn.setObjectName("ActionBtn")
        adv_btn.clicked.connect(self._open_export_dialog)

        layout.addWidget(dl_btn)
        layout.addWidget(srt_btn)
        layout.addWidget(adv_btn)

        # ── VIEW ──────────────────────────────────────────────────────
        layout.addWidget(section("VIEW"))

        self.ts_toggle_btn = QPushButton("\U0001f552  Hide Timestamps")
        self.ts_toggle_btn.setObjectName("ActionBtn")
        self.ts_toggle_btn.clicked.connect(self._toggle_timestamps)

        stats_btn = QPushButton("\U0001f4ca  Stats")
        stats_btn.setObjectName("ActionBtn")
        stats_btn.clicked.connect(self._show_stats)

        layout.addWidget(self.ts_toggle_btn)
        layout.addWidget(stats_btn)

        layout.addWidget(section("FILE"))

        self.edit_btn = QPushButton("\u270f\ufe0f  Edit Transcript")
        self.edit_btn.setObjectName("ActionBtn")
        self.edit_btn.clicked.connect(self._toggle_edit)

        rename_btn = QPushButton("\U0001f3f7\ufe0f  Rename")
        rename_btn.setObjectName("ActionBtn")
        rename_btn.clicked.connect(self._rename)

        delete_btn = QPushButton("\U0001f5d1\ufe0f  Delete")
        delete_btn.setObjectName("ActionBtnDanger")
        delete_btn.clicked.connect(self._delete)

        layout.addWidget(self.edit_btn)
        layout.addWidget(rename_btn)
        layout.addWidget(delete_btn)
        layout.addStretch()

        return panel

    # ─────────────────────────────────────────────────────────────────
    # Public API — called by MainWindow
    # ─────────────────────────────────────────────────────────────────

    def start_streaming(self, transcript_id: str, file_name: str):
        """
        Called by MainWindow when a transcription job starts.
        Sets up the editor for live segment appending.
        """
        self._streaming = True
        self._transcript = {
            "id":               transcript_id,
            "name":             file_name,
            "status":           "processing",
            "segments":         [],
            "duration_seconds": 0,
            "language":         "?",
            "language_confidence": 0,
            "word_count":       0,
        }
        self._show_timestamps = True
        self._editing = False
        self.editor.setReadOnly(True)
        self.edit_btn.setText("\u270f\ufe0f  Edit Transcript")

        self.file_name_label.setText(f"\u23f3  {file_name}")
        self.meta_label.setText("Transcribing \u2014 text will appear here as it is recognised\u2026")

        self.live_progress.setRange(0, 0)   # indeterminate spinner
        self.live_progress.show()
        self.editor.clear()

    def append_segment(self, seg: dict):
        """
        Called for every decoded segment while streaming is active.
        Appends the segment text (with optional timestamp) to the editor
        and scrolls to the bottom so the user always sees the latest text.
        """
        if not self._streaming or self._transcript is None:
            return

        self._transcript["segments"].append(seg)

        if self._show_timestamps:
            line = f"{_readable_time(seg['start'])} {seg['text']}"
        else:
            line = f"{seg['text']}"

        # Move cursor to end and insert — this prevents clearing the whole
        # document on each update (which would reset scroll position).
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)

        prefix = "\n\n" if not self.editor.document().isEmpty() else ""
        self.editor.insertPlainText(prefix + line)

        # Auto-scroll
        self.editor.ensureCursorVisible()

    def update_live_progress(self, current: float, total: float):
        """Keep the thin progress bar in sync with the transcription."""
        if self._streaming:
            self.live_progress.setRange(0, int(total))
            self.live_progress.setValue(int(current))

    def finish_streaming(self, transcript: dict):
        """
        Called when the transcription completes and the full result has
        been merged into storage. Switches the view from streaming mode
        to the normal (read-only / interactive) mode.
        """
        self._streaming = False
        self.live_progress.hide()
        self.load(transcript)

    def load(self, transcript: dict):
        """
        Display a completed (or previously saved) transcript.
        Replaces any streamed content with the authoritative data.
        """
        self._streaming = False
        self._transcript = transcript
        self._show_timestamps = True
        self._editing = False
        self.editor.setReadOnly(True)
        self.edit_btn.setText("\u270f\ufe0f  Edit Transcript")
        self.live_progress.hide()
        
        file_path = transcript.get("file_path")
        self._cleanup_temp_audio()
        if file_path and os.path.exists(file_path):
            self._pending_source_path = file_path
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.audio_widget.show()
        else:
            self._pending_source_path = None
            self.player.setSource(QUrl())
            self.audio_widget.hide()

        lang = str(transcript.get("language", "?")).upper()
        dur  = transcript.get("duration_seconds", 0)
        mins, secs = int(dur // 60), int(dur % 60)
        conf = transcript.get("language_confidence", 0)

        self.file_name_label.setText(transcript.get("name", "Unknown File"))
        self.meta_label.setText(
            f"Language: {lang} ({conf*100:.0f}%)  \u00b7  "
            f"Duration: {mins}:{secs:02d}  \u00b7  {transcript.get('word_count', 0)} words  \u00b7  "
            f"Model: {transcript.get('model', 'large-v3')}"
        )

        self._current_segments = transcript.get("segments", [])

        self._refresh_text()

    # ─────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────

    def _get_proxy_transcript(self):
        proxy = dict(self._transcript)
        proxy["segments"] = getattr(self, "_current_segments", self._transcript.get("segments", []))
        return proxy

    def _refresh_text(self):
        if not self._transcript:
            return
        segs = getattr(self, "_current_segments", self._transcript.get("segments", []))
        
        if self._editing:
            # Flat text for QPlainText editing mode
            text = export_txt(segs, include_timestamps=self._show_timestamps)
            self.editor.setPlainText(text)
        else:
            # Interactive HTML view for clicking timestamps
            html_parts = []
            html_parts.append("<style>a { text-decoration: none; color: #059bc8; }</style>")
            for seg in segs:
                safetext = html.escape(seg.get('text', ''))
                if self._show_timestamps and "start" in seg:
                    # Use first word's start (if available) so clicking skips
                    # any leading silence gap before the speech begins.
                    words = seg.get("words") or []
                    try:
                        if words:
                            start_val = float(words[0].get("start", seg["start"]))
                            end_val = float(words[-1].get("end", seg.get("end", start_val)))
                        else:
                            start_val = float(seg["start"])
                            end_val = float(seg.get("end", start_val))
                    except (ValueError, TypeError):
                        start_val = 0.0
                        end_val = 0.0
                    ts_str = _readable_time(start_val)
                    link = f'<a href="ts:{start_val}:{end_val}">{ts_str}</a>'
                    html_parts.append(f"{link}  {safetext}")
                else:
                    html_parts.append(safetext)
            
            self.editor.setHtml("<br><br>".join(html_parts))

    def _toggle_timestamps(self):
        self._show_timestamps = not self._show_timestamps
        verb = "Hide" if self._show_timestamps else "Show"
        self.ts_toggle_btn.setText(f"\U0001f552  {verb} Timestamps")
        if not self._streaming:
            self._refresh_text()

    def _toggle_edit(self):
        if self._streaming:
            return  # cannot edit while streaming

        self._editing = not self._editing
        self.editor.setReadOnly(not self._editing)
        if self._editing:
            self.edit_btn.setText("\U0001f4be  Save Changes")
            self._refresh_text()
        else:
            # Persist the edited text as a single segment
            edited = self.editor.toPlainText()
            new_segments = [{
                "start": 0.0,
                "end":   self._transcript.get("duration_seconds", 0),
                "text":  edited,
                "words": [],
            }]

            self._transcript["segments"] = new_segments
            self.storage.save(self._transcript)

            self._current_segments = new_segments
            self.edit_btn.setText("\u270f\ufe0f  Edit Transcript")
            self._refresh_text()

    # ─────────────────────────────────────────────────────────────────
    # Audio Playback
    # ─────────────────────────────────────────────────────────────────

    def _seek_to_anchor(self, url: QUrl):
        if url.scheme() == "ts" and self.player.source().isValid():
            try:
                # Encoded as "ts:<start>" or "ts:<start>:<end>".
                raw = url.toString()
                parts = raw.split(":")
                seconds = float(parts[1])
                end_seconds = float(parts[2]) if len(parts) >= 3 else None
                self.player.setPosition(int(seconds * 1000))
                # When an end bound is present, auto-pause at that point so
                # clicking a timestamp plays only that segment.
                self._play_until_ms = int(end_seconds * 1000) if end_seconds else None
                self.player.play()
            except (ValueError, IndexError):
                pass

    def _toggle_playback(self):
        if self.player.isPlaying():
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.NoMedia:
                return
            # Play-All: clear any single-segment auto-pause target.
            self._play_until_ms = None
            self.player.play()
            
    def _on_player_error(self, error, error_string: str):
        import logging
        logging.warning("QMediaPlayer error %s: %s", error, error_string)
        # FormatError means Windows Media Foundation can't decode the file.
        # Try to extract audio to a temp WAV via ffmpeg and reload.
        from PySide6.QtMultimedia import QMediaPlayer as _QMP
        if error == _QMP.Error.FormatError and self._pending_source_path:
            self._extract_audio_with_ffmpeg(self._pending_source_path)

    def _extract_audio_with_ffmpeg(self, source_path: str):
        import logging
        import shutil
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logging.warning("ffmpeg not found on PATH — cannot decode this audio format.")
            return

        self._cleanup_temp_audio()
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        self._temp_audio_path = tmp.name

        self._ffmpeg_proc = QProcess(self)
        self._ffmpeg_proc.finished.connect(self._on_ffmpeg_finished)
        self._ffmpeg_proc.start(
            ffmpeg,
            ["-y", "-i", source_path, "-vn",
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
             self._temp_audio_path],
        )
        logging.info("ffmpeg extracting audio from %s → %s", source_path, self._temp_audio_path)

    def _on_ffmpeg_finished(self, exit_code: int, _exit_status):
        import logging
        if exit_code == 0 and self._temp_audio_path and os.path.exists(self._temp_audio_path):
            logging.info("ffmpeg extraction done — reloading player with WAV.")
            self._pending_source_path = None  # clear BEFORE setSource so error handler won't re-trigger
            self.player.setSource(QUrl.fromLocalFile(self._temp_audio_path))
            self.player.play()
        else:
            logging.warning("ffmpeg extraction failed (exit %s).", exit_code)

    def _cleanup_temp_audio(self):
        if self._temp_audio_path and os.path.exists(self._temp_audio_path):
            try:
                os.unlink(self._temp_audio_path)
            except OSError:
                pass
        self._temp_audio_path = None

    def closeEvent(self, event):
        self._cleanup_temp_audio()
        super().closeEvent(event)

    def _on_playing_changed(self, is_playing: bool):
        self.play_btn.setText("⏸ Pause" if is_playing else "▶ Play")

    def _on_player_position_changed(self, position: int):
        # Auto-pause when a single-segment playback reaches its end.
        if self._play_until_ms is not None and position >= self._play_until_ms:
            self._play_until_ms = None
            self.player.pause()

        if not self.audio_slider.isSliderDown():
            self.audio_slider.setValue(position)
            
        dur = self.player.duration()
        cur_sec = position // 1000
        dur_sec = dur // 1000
        self.time_label.setText(f"{cur_sec // 60:02d}:{cur_sec % 60:02d} / {dur_sec // 60:02d}:{dur_sec % 60:02d}")

    def _quick_download(self):
        if not self._transcript or self._streaming:
            return
        from ui.dialogs.export_dialog import show_quick_download_menu
        show_quick_download_menu(self, self._get_proxy_transcript(), self.sender())

    def _open_srt_dialog(self):
        """Opens the smart SRT re-segmentation download dialog."""
        if not self._transcript or self._streaming:
            return
        from ui.dialogs.srt_dialog import SRTDialog
        SRTDialog(self._get_proxy_transcript(), parent=self).exec()

    def _open_export_dialog(self):
        if not self._transcript or self._streaming:
            return
        ExportDialog(self._get_proxy_transcript(), parent=self).exec()




    def _show_stats(self):
        if not self._transcript:
            return
        from PySide6.QtWidgets import QMessageBox
        t    = self._transcript
        dur  = t.get("duration_seconds", 0)
        mins, secs = int(dur // 60), int(dur % 60)
        conf = t.get("language_confidence", 0)
        QMessageBox.information(
            self, "Transcript Stats",
            f"File:       {t.get('name', 'Unknown')}\n"
            f"Language:   {str(t.get('language','?')).upper()}  "
            f"({conf*100:.1f}% confidence)\n"
            f"Duration:   {mins}:{secs:02d}\n"
            f"Words:      {t.get('word_count', 0)}\n"
            f"Segments:   {len(t.get('segments', []))}\n"
            f"Model:      {t.get('model', 'large-v3')}"
        )

    def _rename(self):
        if not self._transcript:
            return
        dlg = RenameDialog(self._transcript.get("name", ""), parent=self)
        if dlg.exec():
            new_name = dlg.get_name()
            self.storage.rename(self._transcript["id"], new_name)
            self._transcript["name"] = new_name
            self.file_name_label.setText(new_name)
            self.transcript_renamed.emit()

    def _delete(self):
        if not self._transcript:
            return
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Delete Transcript",
            f"Delete '{self._transcript.get('name', 'transcript')}'?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            # Stop playback and release the file handle BEFORE deleting.
            self.player.stop()
            self.player.setSource(QUrl())
            self._play_until_ms = None
            self._pending_source_path = None
            self._cleanup_temp_audio()
            self.audio_widget.hide()

            tid = self._transcript["id"]
            self.storage.delete(tid)
            self.transcript_deleted.emit(tid)
