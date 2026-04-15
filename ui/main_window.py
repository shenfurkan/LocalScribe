from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QSplitter, QStackedWidget, QApplication
)
from PySide6.QtCore import Qt, QThread, QPropertyAnimation, QRect, QEasingCurve
from PySide6.QtGui import QScreen
import os
from pathlib import Path

from ui.sidebar import Sidebar
from ui.dashboard_page import DashboardPage
from ui.transcript_page import TranscriptPage
from core.storage import StorageManager


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LocalScribe")
        self.resize(900, 600)
        self.setMinimumSize(800, 500)
        self._center_window()

        self.storage = StorageManager()

        # ── Central layout: sidebar | stacked content ──────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.sidebar = Sidebar(self.storage)

        self.stack = QStackedWidget()
        self.dashboard_page  = DashboardPage(self.storage)
        self.transcript_page = TranscriptPage(self.storage)
        self.stack.addWidget(self.dashboard_page)    # index 0
        self.stack.addWidget(self.transcript_page)   # index 1

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.stack)
        splitter.setSizes([240, 960])
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)

        root_layout.addWidget(splitter)

        # ── Signal wiring ──────────────────────────────────────────────
        # Sidebar navigation
        self.sidebar.file_selected.connect(self._open_saved_transcript)
        self.sidebar.upload_requested.connect(self._trigger_upload)
        self.sidebar.theme_toggled.connect(self._toggle_theme)

        # ── Live streaming chain ───────────────────────────────────────
        # 1. Transcription starts → switch to transcript page in streaming mode
        self.dashboard_page.streaming_started.connect(self._on_streaming_started)

        # 2. Each segment → forward to transcript page for live append
        self.dashboard_page.segment_received.connect(
            self.transcript_page.append_segment
        )

        # 3. Progress ticks → forward to transcript page thin bar
        self.dashboard_page.progress_received.connect(
            self.transcript_page.update_live_progress
        )

        # 4. Transcription done → reload full data into transcript page
        self.dashboard_page.transcription_completed.connect(
            self._on_transcription_completed
        )

        # File actions
        self.transcript_page.transcript_deleted.connect(self._on_delete)
        self.transcript_page.transcript_renamed.connect(
            self.sidebar.refresh_file_list
        )

        # ── Pre-load the model in the background ───────────────────────
        self._start_model_preload()
        self._apply_current_theme()

    def _apply_current_theme(self):
        theme = self.storage.get_setting("theme", "dark")

        from core.paths import app_bundle_dir
        base_dir = str(app_bundle_dir())

        qss_filename = "dark_theme.qss" if theme == "dark" else "light_theme.qss"
        qss_path = os.path.join(base_dir, "assets", qss_filename)

        app = QApplication.instance()
        if os.path.exists(qss_path):
            with open(qss_path, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
        else:
            print(f"[WARNING] stylesheet not found for theme change: {qss_path}")

        # Update dynamic image assets
        self.sidebar.update_theme(theme, base_dir)
        self.dashboard_page.update_theme(theme, base_dir)

    def _toggle_theme(self):
        current = self.storage.get_setting("theme", "dark")
        new_theme = "light" if current == "dark" else "dark"
        self.storage.set_setting("theme", new_theme)
        self._apply_current_theme()

    # ───────────────────────────────────────────────────────────────────
    # Model pre-load (runs once at startup, silently)
    # ───────────────────────────────────────────────────────────────────

    def _start_model_preload(self):
        """Pre-load the Whisper model into RAM in a background thread.

        By the time the user reaches this point, ``main.py`` has already
        confirmed that the model binary exists on disk (via the setup
        dialog).  This step loads the ~3 GB file into RAM so that the
        first transcription starts instantly instead of stalling.

        Uses the Qt worker-object pattern (moveToThread).  The sidebar
        shows a status indicator while loading is in progress.
        """
        from core.transcriber import ModelPreloadWorker

        self._preload_worker = ModelPreloadWorker()
        self._preload_thread = QThread(self)
        self._preload_worker.moveToThread(self._preload_thread)
        self._preload_thread.started.connect(self._preload_worker.run)

        self._preload_worker.status.connect(self._on_preload_status)
        self._preload_worker.finished.connect(self._on_preload_done)
        self._preload_worker.error.connect(self._on_preload_error)

        self._preload_worker.finished.connect(self._preload_thread.quit)
        self._preload_worker.error.connect(self._preload_thread.quit)
        self._preload_worker.finished.connect(self._preload_worker.deleteLater)
        self._preload_worker.error.connect(self._preload_worker.deleteLater)
        self._preload_thread.finished.connect(self._preload_thread.deleteLater)

        self._preload_thread.start()

    def _on_preload_status(self, msg: str):
        """Update sidebar status while model loads into RAM."""
        self.sidebar.set_status(f"⚙ {msg}")

    def _on_preload_done(self):
        """Model is warm — transcriptions will start instantly."""
        self.sidebar.set_status("✓ Model ready")

    def _on_preload_error(self, err: str):
        """Model failed to load — show a clear error with recovery steps."""
        self.sidebar.set_status("⚠ Model load failed")
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(
            self, "Model Load Error",
            f"The Whisper model could not be loaded:\n\n{err}\n\n"
            "Try restarting LocalScribe. If the problem persists, "
            "delete the models folder and re-run first-time setup."
        )

    # ───────────────────────────────────────────────────────────────────
    # Navigation helpers
    # ───────────────────────────────────────────────────────────────────

    def _center_window(self):
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        if screen.width() > 0:
            x = screen.x() + (screen.width() - size.width()) // 2
            y = screen.y() + (screen.height() - size.height()) // 2
            self.move(max(0, x), max(0, y))

    def _expand_window(self):
        screen = QApplication.primaryScreen().geometry()
        if self.width() < 1100 and screen.width() > 1150:
            current_geom = self.geometry()
            target_width = 1200
            target_height = min(750, screen.height() - 80)
            
            x = current_geom.x() - (target_width - current_geom.width()) // 2
            y = current_geom.y() - (target_height - current_geom.height()) // 2
            
            x = max(screen.x() + 40, x)
            y = max(screen.y() + 40, y)
            
            target_geom = QRect(x, y, target_width, target_height)
            
            self.anim = QPropertyAnimation(self, b"geometry")
            self.anim.setDuration(450)
            self.anim.setStartValue(current_geom)
            self.anim.setEndValue(target_geom)
            self.anim.setEasingCurve(QEasingCurve.OutCubic)
            self.anim.start()

    def _trigger_upload(self):
        """Sidebar '+ New Transcription' button."""
        self.stack.setCurrentIndex(0)
        self.dashboard_page.trigger_upload()

    def _open_saved_transcript(self, transcript_id: str):
        """Sidebar file card clicked — open a completed transcript."""
        data = self.storage.load(transcript_id)
        if not data:
            return
        if data.get("status") == "processing":
            # Already showing live view — just switch to it
            self._expand_window()
            self.stack.setCurrentIndex(1)
            return
        self.transcript_page.load(data)
        self._expand_window()
        self.stack.setCurrentIndex(1)

    # ───────────────────────────────────────────────────────────────────
    # Live streaming callbacks
    # ───────────────────────────────────────────────────────────────────

    def _on_streaming_started(self, transcript_id: str, file_name: str):
        """
        Called as soon as a file is queued for transcription.
        Switches immediately to the transcript page in streaming mode so
        the user sees text appearing word-by-word in real time.
        """
        self.transcript_page.start_streaming(transcript_id, file_name)
        self._expand_window()
        self.stack.setCurrentIndex(1)
        # Refresh the sidebar so the new card appears right away
        self.sidebar.refresh_file_list()

    def _on_transcription_completed(self, transcript_id: str):
        """
        Called when the worker finishes and storage has been updated.
        Replaces the live view with the final authoritative transcript.
        """
        self.sidebar.refresh_file_list()
        data = self.storage.load(transcript_id)
        if data:
            self.transcript_page.finish_streaming(data)

    # ───────────────────────────────────────────────────────────────────

    def _on_delete(self, transcript_id: str):
        if self.dashboard_page._current_transcript_id == transcript_id:
            self.dashboard_page._cancel()

        self.stack.setCurrentIndex(0)
        self.sidebar.refresh_file_list()
