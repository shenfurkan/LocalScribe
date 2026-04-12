from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QTextEdit, QFileDialog,
    QPushButton, QLabel, QProgressBar, QComboBox
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QTextCursor

from ui.widgets.timestamp_highlighter import TimestampHighlighter
from ui.dialogs.export_dialog import ExportDialog
from ui.dialogs.translate_dialog import TranslateDialog
from ui.dialogs.rename_dialog import RenameDialog
from core.exporter import export_txt, _readable_time
from core.storage import StorageManager


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

        self.editor = QTextEdit()
        self.editor.setObjectName("TranscriptEditor")
        self.editor.setReadOnly(True)

        # Attach syntax highlighter — colours [HH:MM:SS] tokens violet
        self.highlighter = TimestampHighlighter(self.editor.document())

        header_layout = QHBoxLayout()
        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(2)
        header_text_layout.addWidget(self.file_name_label)
        header_text_layout.addWidget(self.meta_label)

        self.version_combo = QComboBox()
        self.version_combo.setFixedWidth(200)
        self.version_combo.currentIndexChanged.connect(self._on_version_changed)
        self.version_combo.hide()

        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()
        header_layout.addWidget(self.version_combo)

        editor_layout.addLayout(header_layout)
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

        # ── TOOLS ─────────────────────────────────────────────────────
        layout.addWidget(section("TOOLS"))

        translate_btn = QPushButton("\U0001f310  Translate")
        translate_btn.setObjectName("ActionBtn")
        translate_btn.clicked.connect(self._open_translate_dialog)

        share_btn = QPushButton("\U0001f517  Share (HTML)")
        share_btn.setObjectName("ActionBtn")
        share_btn.clicked.connect(self._share_as_html)

        layout.addWidget(translate_btn)
        layout.addWidget(share_btn)

        # ── FILE ──────────────────────────────────────────────────────
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

        self.version_combo.hide()
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

        lang = str(transcript.get("language", "?")).upper()
        dur  = transcript.get("duration_seconds", 0)
        wc   = transcript.get("word_count", 0)
        mins, secs = int(dur // 60), int(dur % 60)
        conf = transcript.get("language_confidence", 0)

        self.file_name_label.setText(transcript.get("name", "Unknown File"))
        self.meta_label.setText(
            f"Language: {lang} ({conf*100:.0f}%)  \u00b7  "
            f"Duration: {mins}:{secs:02d}  \u00b7  {wc} words  \u00b7  "
            f"Model: {transcript.get('model', 'large-v3')}"
        )

        self._current_segments = transcript.get("segments", [])

        self.version_combo.blockSignals(True)
        self.version_combo.clear()

        self.version_combo.addItem(f"Original ({lang})", userData={"type": "original"})

        if "translated_versions" in transcript and transcript["translated_versions"]:
            for lang_code, t_data in transcript["translated_versions"].items():
                self.version_combo.addItem(
                    f"Translation ({lang_code.upper()})",
                    userData={"type": "translation", "code": lang_code, "segments": t_data["segments"]}
                )
            self.version_combo.show()
        else:
            self.version_combo.hide()

        self.version_combo.blockSignals(False)
        self.version_combo.setCurrentIndex(0)

        self._refresh_text()

    # ─────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────

    def _on_version_changed(self, idx: int):
        if idx < 0 or not self._transcript:
            return
        data = self.version_combo.itemData(idx)
        if data["type"] == "original":
            self._current_segments = self._transcript.get("segments", [])
        else:
            self._current_segments = data["segments"]
        self._refresh_text()

    def _get_proxy_transcript(self):
        proxy = dict(self._transcript)
        proxy["segments"] = getattr(self, "_current_segments", self._transcript.get("segments", []))
        idx = max(0, self.version_combo.currentIndex())
        if idx > 0:
            data = self.version_combo.itemData(idx)
            proxy["name"] = f"{proxy['name']} ({data['code'].upper()})"
        return proxy

    def _refresh_text(self):
        if not self._transcript:
            return
        segs = getattr(self, "_current_segments", self._transcript.get("segments", []))
        text = export_txt(segs, include_timestamps=self._show_timestamps)
        self.editor.setPlainText(text)

    def _toggle_timestamps(self):
        self._show_timestamps = not self._show_timestamps
        verb = "Hide" if self._show_timestamps else "Show"
        self.ts_toggle_btn.setText(f"\U0001f552  {verb} Timestamps")
        if not self._streaming:
            self._refresh_text()

    def _toggle_edit(self):
        if self._streaming:
            return  # cannot edit while streaming

        idx = max(0, self.version_combo.currentIndex())
        data = self.version_combo.itemData(idx) if self.version_combo.count() > 0 else {"type": "original"}

        self._editing = not self._editing
        self.editor.setReadOnly(not self._editing)
        if self._editing:
            self.edit_btn.setText("\U0001f4be  Save Changes")
            self.version_combo.setEnabled(False)
        else:
            # Persist the edited text as a single segment
            edited = self.editor.toPlainText()
            new_segments = [{
                "start": 0.0,
                "end":   self._transcript.get("duration_seconds", 0),
                "text":  edited,
                "words": [],
            }]

            if data["type"] == "original":
                self._transcript["segments"] = new_segments
                self.storage.save(self._transcript)
            else:
                code = data["code"]
                self.storage.save_translation(self._transcript["id"], code, new_segments)
                self._transcript["translated_versions"][code]["segments"] = new_segments
                data["segments"] = new_segments
                self.version_combo.setItemData(idx, data)

            self._current_segments = new_segments
            self.edit_btn.setText("\u270f\ufe0f  Edit Transcript")
            self.version_combo.setEnabled(True)

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

    def _open_translate_dialog(self):
        if not self._transcript or self._streaming:
            return
        dlg = TranslateDialog(self._transcript, self.storage, parent=self)
        dlg.translation_saved.connect(self.load)
        dlg.exec()

    def _share_as_html(self):
        if not self._transcript or self._streaming:
            return
        from core.exporter import export_html
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Shareable HTML",
            f"{self._transcript.get('name', 'transcript')}.html",
            "HTML Files (*.html)",
        )
        if path:
            proxy = self._get_proxy_transcript()
            html = export_html(
                proxy.get("name", "Transcript"),
                proxy.get("segments", []),
            )
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)

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
            tid = self._transcript["id"]
            self.storage.delete(tid)
            self.transcript_deleted.emit(tid)
