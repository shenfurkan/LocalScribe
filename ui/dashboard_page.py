import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton
)
from PySide6.QtCore import Signal, QThread, Qt
from ui.widgets.drop_zone import DropZone
from core.storage import StorageManager


class DashboardPage(QWidget):
    """Landing page with a drop-zone for starting new transcriptions.

    Signal chain
    ------------
    1. User drops a file → ``_on_files_dropped`` → language dialog.
    2. On confirm → ``_start_transcription``:
       a. **Pre-flight check**: ``is_model_ready()`` gates the attempt.
       b. A ``TranscriptionWorker`` is moved to a new ``QThread``.
       c. ``streaming_started`` tells ``MainWindow`` to switch to the
          transcript page in live-streaming mode.
    3. Worker emits segments → ``segment_received`` → ``TranscriptPage``.
    4. Worker finishes → ``transcription_completed`` → persist to storage.
    """

    # Emitted once the stub is saved and the thread starts.
    # Payload: (transcript_id, file_name).
    streaming_started = Signal(str, str)

    # Re-emitted from the worker so MainWindow can route to TranscriptPage.
    segment_received = Signal(dict)
    progress_received = Signal(float, float)

    # Emitted once the worker finishes and the record is persisted.
    transcription_completed = Signal(str)   # transcript_id

    def __init__(self, storage: StorageManager):
        super().__init__()
        self.storage = storage
        self._active_worker = None
        self._active_thread: QThread | None = None
        self._current_transcript_id: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────
        header = QLabel("Drop a file to start transcribing")
        header.setObjectName("DashboardHeader")
        layout.addWidget(header)

        sub = QLabel(
            "faster\u2011whisper large\u2011v3  ·  100 % local  ·  no internet required"
        )
        sub.setObjectName("DashboardSub")
        layout.addWidget(sub)

        # ── Drop zone ──────────────────────────────────────────────────
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._on_files_dropped)
        layout.addWidget(self.drop_zone, stretch=1)

        # ── Progress area (hidden until a job starts) ──────────────────
        self.progress_container = QWidget()
        self.progress_container.hide()
        prog_layout = QVBoxLayout(self.progress_container)
        prog_layout.setSpacing(8)

        self.status_label = QLabel("Initialising…")
        self.status_label.setStyleSheet("color: #718096; font-size: 14pt; font-weight: 500;") # Increased size
        self.status_label.setAlignment(Qt.AlignCenter)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(90)
        cancel_btn.clicked.connect(self._cancel)

        prog_layout.addWidget(self.status_label)
        prog_layout.addWidget(cancel_btn)

        layout.addWidget(self.progress_container)

    # ───────────────────────────────────────────────────────────────────
    # Public API
    # ───────────────────────────────────────────────────────────────────

    def update_theme(self, theme: str, base_dir: str):
        filename = "dragdropdarktheme.png" if theme == "dark" else "dragdroplighttheme.png"
        path = os.path.join(base_dir, "image", filename)
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import Qt
        pix = QPixmap(path)
        if not pix.isNull():
            self.drop_zone.icon_label.setPixmap(pix.scaledToHeight(80, Qt.SmoothTransformation))

    def trigger_upload(self):
        """Called when the sidebar '+ New Transcription' button is pressed."""
        from PySide6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Audio / Video Files", "",
            "Media Files (*.mp3 *.wav *.m4a *.ogg *.flac *.mp4 *.mkv *.avi *.mov *.webm *.aac);;All Files (*.*)"
        )
        if paths:
            self._on_files_dropped(paths)

    # ───────────────────────────────────────────────────────────────────
    # Internal helpers
    # ───────────────────────────────────────────────────────────────────

    def _on_files_dropped(self, paths: list[str]):
        if not paths:
            return
            
        if len(paths) > 1:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Multiple Files",
                "Batch processing is not supported yet.\nOnly the first file will be transcribed."
            )
            
        # Only process the first file; batch support can be added later
        file_path = paths[0]
        
        # Pop the settings dialog first
        from ui.dialogs.language_dialog import LanguageDialog
        dialog = LanguageDialog(os.path.basename(file_path), self)
        if dialog.exec():
            # Pass all gathered settings into a config structured dictionary
            settings = {
                "language": dialog.selected_code,
                "prompt": dialog.initial_prompt,
                "beam_size": dialog.beam_size,
                "profile": dialog.transcription_profile,
            }
            self._start_transcription(file_path, settings)

    def _start_transcription(self, file_path: str, settings: dict):
        """Create a TranscriptionWorker and start it on a background QThread.

        Three safety gates run before the worker starts:
        1. **Busy check** — only one transcription at a time.
        2. **Model readiness** — ``is_model_ready()`` verifies the binary
           exists on disk and is >= 1 GB.  Without this, the worker would
           appear to hang while ``get_model()`` raises an error internally.
        3. **Backend import** — catches ImportError if the transcription
           backend (faster-whisper, CTranslate2) is broken or missing.
        """
        if self._active_thread and self._active_thread.isRunning():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Busy",
                "A transcription is already running.\n"
                "Please wait for it to finish or cancel it first."
            )
            return

        # PRE-FLIGHT: verify model is present before launching the worker.
        # This prevents the "hung transcription" bug where the UI would
        # show "Loading model..." forever with no error dialog.
        from core.setup_manager import is_model_ready
        if not is_model_ready():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Model Not Ready",
                "Whisper model is not installed yet (or is incomplete).\n\n"
                "Please restart LocalScribe and complete first-time setup before transcribing."
            )
            return

        try:
            from core.transcriber import TranscriptionWorker
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Backend Load Error",
                "LocalScribe could not load the transcription backend.\n\n"
                f"Details: {exc}"
            )
            return

        file_name = os.path.basename(file_path)

        # ── 1.  Persist a stub so the sidebar card appears immediately ──
        self._current_transcript_id = self.storage.save({
            "name":   file_name,
            "status": "processing",
            "segments": [],
            "file_path": file_path,
        })

        # ── 2.  Show progress UI ────────────────────────────────────────
        self.drop_zone.hide()
        self.progress_container.show()
        self.status_label.setText(
            "Loading Whisper engine…  (this may take a moment)"
        )

        # ── 3.  Notify MainWindow → switch to TranscriptPage live view ──
        # This signal is emitted to notify MainWindow to switch to the TranscriptPage
        # in live-streaming mode. The payload contains the transcript ID and file name.
        self.streaming_started.emit(self._current_transcript_id, file_name)

        # ── 4.  Build and start the worker (Qt worker-object pattern) ──
        # Create a TranscriptionWorker instance and move it to a new QThread.
        # This allows the worker to run in the background without blocking the UI.
        self._active_worker = TranscriptionWorker(
            file_path, 
            audio_language=settings.get("language"),
            initial_prompt=settings.get("prompt", ""),
            beam_size=settings.get("beam_size", 5),
            profile=settings.get("profile", "balanced"),
        )
        self._active_thread = QThread(self)
        self._active_worker.moveToThread(self._active_thread)
        self._active_thread.started.connect(self._active_worker.run)

        # Wire worker signals → UI slots.  All connections are queued
        # because the worker lives on a different thread.
        self._active_worker.transcription_started.connect(self._on_transcription_started)
        self._active_worker.segment_ready.connect(self._on_segment_ready)
        self._active_worker.progress_updated.connect(self._update_progress)
        self._active_worker.finished.connect(self._on_finished)
        self._active_worker.error.connect(self._on_error)

        # Worker cleanup: quit the thread and schedule deleteLater()
        # on every terminal signal (finished, error, cancelled).
        for sig in (
            self._active_worker.finished,
            self._active_worker.error,
            self._active_worker.cancelled,
        ):
            sig.connect(self._active_thread.quit)
            sig.connect(self._active_worker.deleteLater)
        self._active_thread.finished.connect(self._active_thread.deleteLater)

        # Cancelled → reset the dashboard UI immediately.
        self._active_worker.cancelled.connect(self._reset_ui)

        self._active_thread.start()

    def _cancel(self):
        if self._active_worker:
            self._active_worker.cancel()
        if self._current_transcript_id:
            self.storage.delete(self._current_transcript_id)
        self._reset_ui()

    def _reset_ui(self):
        self.progress_container.hide()
        self.drop_zone.show()
        self._active_worker = None
        self._active_thread = None
        self._current_transcript_id = None

    # ───────────────────────────────────────────────────────────────────
    # Worker signal slots
    # ───────────────────────────────────────────────────────────────────

    def _on_transcription_started(self, total_duration: float):
        """Model is loaded; transcription has begun — switch bar to percentage."""
        mins = int(total_duration // 60)
        secs = int(total_duration % 60)
        self.status_label.setText(
            f"Transcribing…  (audio length: {mins}:{secs:02d})"
        )

    def _on_segment_ready(self, seg: dict):
        """Re-emit so MainWindow can forward the segment to TranscriptPage."""
        self.segment_received.emit(seg)

    def _update_progress(self, current: float, total: float):
        # self.progress_bar.setValue(int(current)) # progress bar removed
        self.progress_received.emit(current, total)

    def _on_finished(self, result: dict):
        # Capture the ID *before* _reset_ui() clears it.
        tid = self._current_transcript_id

        if not tid:
            self._reset_ui()
            return

        # Merge transcription result into the stored stub and mark ready.
        data = self.storage.load(tid)
        if data:
            data.update(result)
            data["status"] = "ready"
            self.storage.save(data)

        self._reset_ui()
        self.transcription_completed.emit(tid)

    def _on_error(self, err_msg: str):
        if self._current_transcript_id is None:
            self._reset_ui()
            return
            
        data = self.storage.load(self._current_transcript_id)
        if data:
            data["status"] = "failed"
            data["error_message"] = err_msg
            self.storage.save(data)

        self._reset_ui()

        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(
            self, "Transcription Failed",
            f"An error occurred during transcription:\n\n{err_msg}"
        )
