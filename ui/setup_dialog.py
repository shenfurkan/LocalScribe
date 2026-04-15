"""
ui/setup_dialog.py — Modal first-run setup dialog.

Shown when ``is_model_ready()`` returns False at startup (see ``main.py``).
Runs ``SetupWorker`` in a background ``QThread`` and relays its signals
to the progress bar and status label.

Lifecycle
---------
1. ``showEvent`` → starts the worker thread.
2. Worker emits ``status_update`` / ``progress`` / ``log_update`` → UI updates.
3. Worker emits ``finished(success, error_msg)``:
   - **Success:** button becomes “Continue”, ``_success`` is set True.
   - **Failure:** button becomes “Close”, error is displayed.
4. User clicks the button → ``accept()`` closes the dialog.
5. ``main.py`` reads ``dlg.setup_succeeded`` to decide whether to proceed.

Cancel safety
-------------
If the user clicks “Cancel” while the worker is running:
- ``closeEvent`` intercepts, disables the button, and calls
  ``worker.request_cancel()``.
- The worker checks ``_cancel_requested`` between file downloads and
  raises ``RuntimeError``, which triggers cleanup of partial files.
- ``_on_finished(False, ...)`` re-enables the button so the dialog
  never gets stuck.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QTextEdit
)
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QFont

from core.setup_manager import SetupWorker


class SetupDialog(QDialog):
    """Non-closeable progress dialog for first-run model download.

    Uses the Qt worker-object pattern (``moveToThread``) as recommended
    by the official Qt documentation:
    https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LocalScribe — First-Time Setup")
        self.setFixedSize(720, 480)
        # Remove the close (X) button to prevent accidental dismissal
        # during a multi-GB download.  The only way to close is through
        # the Cancel / Continue button.
        self.setWindowFlags(
            Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint
        )
        self._success = False
        self._error_msg = ""

        # ── Layout ────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(14)

        title = QLabel("Setting up LocalScribe")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        self._status_label = QLabel("Preparing...")
        self._status_label.setWordWrap(True)
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._status_label)

        # Range (0, 0) = indeterminate (bouncing) animation.
        # Switched to (0, 100) once we know the total download size.
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("Preparing…")
        self._progress_bar.setFixedHeight(18)
        layout.addWidget(self._progress_bar)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setPlaceholderText(
            "Download details will appear here (source link, size, speed, saved files)..."
        )
        self._log_view.setMinimumHeight(220)
        layout.addWidget(self._log_view)

        self._close_btn = QPushButton("Close")
        self._close_btn.setFixedWidth(100)
        self._close_btn.setVisible(True)
        self._close_btn.setText("Cancel")
        self._close_btn.clicked.connect(self.accept)
        layout.addWidget(self._close_btn, alignment=Qt.AlignCenter)

        # ── Worker thread (Qt worker-object pattern) ──────────────────
        # The worker is created on the main thread, then moved to a new
        # QThread.  Signal–slot connections across threads use Qt’s
        # queued connection mechanism, making them fully thread-safe.
        self._thread = QThread()
        self._worker = SetupWorker()
        self._worker.moveToThread(self._thread)

        # QThread.started fires once the event loop begins in the new
        # thread, which triggers worker.run() via queued connection.
        self._thread.started.connect(self._worker.run)
        self._worker.status_update.connect(self._on_status)
        self._worker.log_update.connect(self._on_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)

    def showEvent(self, event):
        super().showEvent(event)
        self._thread.start()

    def closeEvent(self, event):
        """Intercept window close while the worker is running.

        Instead of immediately closing (which would orphan the thread),
        we request cancellation and ignore the close event.  The worker
        will finish, ``_on_finished`` will re-enable the button, and
        the user can then close normally.
        """
        if self._thread.isRunning():
            self._status_label.setText("Cancelling setup and cleaning partial files...")
            self._close_btn.setEnabled(False)
            self._worker.request_cancel()
            event.ignore()
            return
        super().closeEvent(event)

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_status(self, text: str):
        self._status_label.setText(text)

    def _on_log(self, text: str):
        self._log_view.append(text)

    def _on_progress(self, value: int):
        if value < 0:
            self._progress_bar.setRange(0, 0)  # indeterminate
            self._progress_bar.setFormat("Preparing…")
        else:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(value)
            self._progress_bar.setFormat("%p%")

    def _on_finished(self, success: bool, error_msg: str):
        """Handle worker completion (success or failure).

        Always re-enables the close button to prevent the dialog from
        becoming stuck — this was a real bug that left users unable to
        dismiss a failed setup.
        """
        self._thread.quit()
        self._thread.wait()  # block until the thread actually exits

        self._success = success
        self._error_msg = error_msg

        if success:
            self._status_label.setText("Setup complete! LocalScribe is ready.")
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(100)
            self._close_btn.setText("Continue")
            self._log_view.append("Setup complete.")
        else:
            self._status_label.setText(
                f"Setup failed:\n{error_msg}\n\n"
                "Please try again. This can happen due to a slow connection, "
                "temporary Hugging Face server load, antivirus scanning, "
                "or disk write delays."
            )
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(0)
            self._close_btn.setText("Close")
            self._log_view.append(f"Setup failed: {error_msg}")

        # CRITICAL: always re-enable the button.  If the user cancelled
        # (which disabled the button), they need a way out.
        self._close_btn.setEnabled(True)
        self._close_btn.setVisible(True)

    @property
    def setup_succeeded(self) -> bool:
        """True only if the worker reported success."""
        return self._success
