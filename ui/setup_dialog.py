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
   - **Success:** button becomes "Continue", ``_success`` is set True.
   - **Failure:** button becomes "Close", error is displayed.
4. User clicks the button → ``accept()`` closes the dialog.
5. ``main.py`` reads ``dlg.setup_succeeded`` to decide whether to proceed.

Cancel safety
-------------
If the user clicks "Cancel" while the worker is running:
- ``closeEvent`` intercepts, disables the button, and calls
  ``worker.request_cancel()``.
- The worker checks ``_cancel_requested`` between file downloads and
  raises ``RuntimeError``, which triggers cleanup of partial files.
- ``_on_finished(False, ...)`` re-enables the button so the dialog
  never gets stuck.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QTextEdit, QFrame
)
from PySide6.QtCore import Qt, QThread, QTimer

from core.setup_manager import SetupWorker


# ── Inline stylesheet for the setup dialog ────────────────────────────────────
# Applied directly so it works before the main theme is loaded.
_SETUP_DIALOG_QSS = """
QDialog#SetupDialog {
    background-color: #F6F8FB;
}

#SetupTitle {
    font-size: 16pt;
    font-weight: 700;
    color: #0F172A;
    background-color: transparent;
}

#SetupSubtitle {
    font-size: 9.5pt;
    color: #475569;
    background-color: transparent;
}

#HardwareCard {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    padding: 10px 14px;
}

#HardwareIcon {
    font-size: 13pt;
    color: #0EA5E9;
    background-color: transparent;
}

#HardwareLabel {
    font-size: 8pt;
    color: #64748B;
    background-color: transparent;
    font-weight: 600;
    letter-spacing: 0.6px;
}

#HardwareValue {
    font-size: 9.5pt;
    color: #0F172A;
    background-color: transparent;
    font-weight: 500;
}

#StatusLabel {
    font-size: 10pt;
    font-weight: 600;
    color: #0F172A;
    background-color: transparent;
}

#FileLabel {
    font-size: 9pt;
    color: #475569;
    background-color: transparent;
}

#PercentLabel {
    font-size: 18pt;
    font-weight: 700;
    color: #0EA5E9;
    background-color: transparent;
}

#ProgressMeta {
    font-size: 9pt;
    color: #64748B;
    background-color: transparent;
}

#SetupProgress {
    background-color: #E2E8F0;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    min-height: 10px;
    max-height: 10px;
}

#SetupProgress::chunk {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #0284C7, stop: 1 #0EA5E9
    );
    border-radius: 4px;
}

#LogView {
    background-color: #FFFFFF;
    color: #334155;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 8pt;
    padding: 8px;
    selection-background-color: #0EA5E9;
}

#CancelBtn {
    background-color: #FFFFFF;
    color: #0F172A;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: 600;
    font-size: 9.5pt;
}
#CancelBtn:hover {
    background-color: #F8FAFC;
    border-color: #94A3B8;
}
#CancelBtn:pressed {
    background-color: #EEF2F7;
}

#ContinueBtn {
    background-color: #0284C7;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: 700;
    font-size: 9.5pt;
}
#ContinueBtn:hover {
    background-color: #0EA5E9;
}
#ContinueBtn:pressed {
    background-color: #0369A1;
}

#Separator {
    background-color: #E2E8F0;
    max-height: 1px;
}
"""


class SetupDialog(QDialog):
    """Non-closeable progress dialog for first-run model download.

    Uses the Qt worker-object pattern (``moveToThread``) as recommended
    by the official Qt documentation:
    https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SetupDialog")
        self.setWindowTitle("LocalScribe — First-Time Setup")
        self.setFixedSize(820, 430)
        # Remove the close (X) button to prevent accidental dismissal
        # during a multi-GB download.  The only way to close is through
        # the Cancel / Continue button.
        self.setWindowFlags(
            Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint
        )
        self.setStyleSheet(_SETUP_DIALOG_QSS)
        self._success = False
        self._error_msg = ""

        # ── Layout ────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(0)

        # Title row
        title = QLabel("Setting up LocalScribe")
        title.setObjectName("SetupTitle")
        title.setAlignment(Qt.AlignLeft)
        layout.addWidget(title)
        layout.addSpacing(4)

        subtitle = QLabel("Downloading AI model and configuring hardware acceleration")
        subtitle.setObjectName("SetupSubtitle")
        subtitle.setAlignment(Qt.AlignLeft)
        layout.addWidget(subtitle)
        layout.addSpacing(12)

        # ── Hardware detection card ───────────────────────────────────
        hw_card = QFrame()
        hw_card.setObjectName("HardwareCard")
        hw_layout = QHBoxLayout(hw_card)
        hw_layout.setContentsMargins(14, 10, 14, 10)
        hw_layout.setSpacing(14)

        hw_icon = QLabel("\u26A1")
        hw_icon.setObjectName("HardwareIcon")
        hw_icon.setFixedWidth(32)
        hw_icon.setAlignment(Qt.AlignCenter)
        hw_layout.addWidget(hw_icon)

        hw_text_layout = QVBoxLayout()
        hw_text_layout.setSpacing(2)
        self._hw_label = QLabel("HARDWARE")
        self._hw_label.setObjectName("HardwareLabel")
        hw_text_layout.addWidget(self._hw_label)
        self._hw_value = QLabel("Detecting GPU...")
        self._hw_value.setObjectName("HardwareValue")
        hw_text_layout.addWidget(self._hw_value)
        hw_layout.addLayout(hw_text_layout, 1)

        layout.addWidget(hw_card)
        layout.addSpacing(12)

        # ── Separator ─────────────────────────────────────────────────
        sep = QFrame()
        sep.setObjectName("Separator")
        sep.setFixedHeight(1)
        layout.addWidget(sep)
        layout.addSpacing(18)

        # ── Status + percentage row ───────────────────────────────────
        status_row = QHBoxLayout()
        status_row.setSpacing(12)

        status_col = QVBoxLayout()
        status_col.setSpacing(3)
        self._status_label = QLabel("Preparing...")
        self._status_label.setObjectName("StatusLabel")
        self._status_label.setWordWrap(True)
        status_col.addWidget(self._status_label)

        self._file_label = QLabel("")
        self._file_label.setObjectName("FileLabel")
        self._file_label.setWordWrap(True)
        status_col.addWidget(self._file_label)
        status_row.addLayout(status_col, 1)

        self._percent_label = QLabel("")
        self._percent_label.setObjectName("PercentLabel")
        self._percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._percent_label.setFixedWidth(80)
        status_row.addWidget(self._percent_label)

        layout.addLayout(status_row)
        layout.addSpacing(8)

        # ── Progress bar ──────────────────────────────────────────────
        # Range (0, 0) = indeterminate (bouncing) animation.
        # Switched to (0, 100) once we know the total download size.
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("SetupProgress")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(10)
        layout.addWidget(self._progress_bar)

        self._progress_meta_label = QLabel("0% complete • 100% remaining")
        self._progress_meta_label.setObjectName("ProgressMeta")
        self._progress_meta_label.setAlignment(Qt.AlignLeft)
        layout.addWidget(self._progress_meta_label)
        layout.addSpacing(10)

        # ── Log viewer ────────────────────────────────────────────────
        self._log_view = QTextEdit()
        self._log_view.setObjectName("LogView")
        self._log_view.setReadOnly(True)
        self._log_view.setPlaceholderText(
            "Download activity will appear here..."
        )
        self._log_view.setMinimumHeight(88)
        self._log_view.setMaximumHeight(96)
        layout.addWidget(self._log_view, 1)
        layout.addSpacing(10)

        # ── Button row ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._close_btn = QPushButton("Cancel")
        self._close_btn.setObjectName("CancelBtn")
        self._close_btn.setFixedWidth(120)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Detect hardware (non-blocking) ────────────────────────────
        QTimer.singleShot(100, self._detect_hardware)

        # ── Worker thread (Qt worker-object pattern) ──────────────────
        # The worker is created on the main thread, then moved to a new
        # QThread.  Signal–slot connections across threads use Qt's
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
        self._worker.file_status.connect(self._on_file_status)
        self._worker.finished.connect(self._on_finished)

    def _detect_hardware(self):
        """Probe GPU hardware and update the hardware card."""
        try:
            from core.gpu_manager import detect_gpu
            info = detect_gpu()
            if info.cuda_available:
                vram_text = f"{info.vram_total_mb} MB" if info.vram_total_mb else ""
                self._hw_value.setText(
                    f"{info.device_name}"
                    + (f"  \u2022  {vram_text} VRAM" if vram_text else "")
                    + "  \u2022  CUDA Enabled"
                )
                self._hw_value.setStyleSheet("color: #06d6a0;")
            else:
                self._hw_value.setText("No GPU detected \u2014 using CPU mode")
                self._hw_value.setStyleSheet("color: #FBBF24;")
        except Exception:
            self._hw_value.setText("Hardware detection unavailable")
            self._hw_value.setStyleSheet("color: #94A3B8;")

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
            self._status_label.setText("Cancelling setup...")
            self._file_label.setText("Cleaning up partial files...")
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
        # Auto-scroll to bottom
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_file_status(self, text: str):
        self._file_label.setText(text)

    def _on_progress(self, value: int):
        if value < 0:
            self._progress_bar.setRange(0, 0)
            self._percent_label.setText("--")
            self._progress_meta_label.setText("Calculating total size...")
        else:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(value)
            self._percent_label.setText(f"{value}%")
            remaining = max(0, 100 - value)
            self._progress_meta_label.setText(
                f"{value}% complete • {remaining}% remaining"
            )

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
            self._status_label.setText("Setup complete!")
            self._status_label.setStyleSheet(
                "color: #06d6a0; font-size: 12pt; font-weight: 700;"
            )
            self._file_label.setText("LocalScribe is ready to use.")
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(100)
            self._percent_label.setText("100%")
            self._progress_meta_label.setText("100% complete • 0% remaining")
            self._close_btn.setText("Continue")
            self._close_btn.setObjectName("ContinueBtn")
            self._close_btn.setStyle(self._close_btn.style())
            self._log_view.append("\n\u2714 Setup completed successfully.")
        else:
            self._status_label.setText("Setup failed")
            self._status_label.setStyleSheet(
                "color: #F87171; font-size: 12pt; font-weight: 700;"
            )
            self._file_label.setText(
                f"{error_msg}\n\n"
                "This can happen due to a slow connection, server load, "
                "antivirus scanning, or disk write delays."
            )
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(0)
            self._percent_label.setText("0%")
            self._progress_meta_label.setText("0% complete • 100% remaining")
            self._close_btn.setText("Close")
            self._log_view.append(f"\n\u2718 Setup failed: {error_msg}")

        # CRITICAL: always re-enable the button.  If the user cancelled
        # (which disabled the button), they need a way out.
        self._close_btn.setEnabled(True)
        self._close_btn.setVisible(True)

    @property
    def setup_succeeded(self) -> bool:
        """True only if the worker reported success."""
        return self._success
