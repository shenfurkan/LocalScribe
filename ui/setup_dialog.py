from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QThread
from core.setup_manager import SetupWorker

class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preparing LocalScribe")
        self.setFixedSize(500, 200)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setStyleSheet("""
            QDialog { background-color: #0f172a; }
            QLabel#Title { font-family: 'Segoe UI', Arial, sans-serif; font-size: 14pt; font-weight: bold; color: #f8fafc; }
            QLabel#Subtitle { font-size: 9pt; color: #94a3b8; }
            QProgressBar {
                border: 1px solid #334155;
                border-radius: 4px;
                background-color: #1e293b;
                text-align: center;
                color: transparent;
                height: 12px;
            }
            QProgressBar::chunk {
                background-color: #0ea5e9;
                border-radius: 3px;
            }
            QLabel#Percent { font-size: 16pt; font-weight: bold; color: #0ea5e9; }
            QPushButton {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 6px 20px;
                color: #f8fafc;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #334155; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        self.title_label = QLabel("Initializing Environment")
        self.title_label.setObjectName("Title")
        layout.addWidget(self.title_label)

        self.subtitle_label = QLabel("Please wait while required models are downloaded.")
        self.subtitle_label.setObjectName("Subtitle")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

        layout.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        bottom_layout = QHBoxLayout()
        self.file_label = QLabel("Checking requirements...")
        self.file_label.setStyleSheet("color: #94a3b8; font-size: 8pt;")
        bottom_layout.addWidget(self.file_label)
        bottom_layout.addStretch()
        
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("Percent")
        bottom_layout.addWidget(self.percent_label)
        
        layout.addLayout(bottom_layout)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.action_btn = QPushButton("Cancel")
        self.action_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.action_btn)
        layout.addLayout(button_layout)

        self._success = False
        self._thread = QThread()
        self._worker = SetupWorker()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.status_update.connect(self.title_label.setText)
        self._worker.file_status.connect(self.file_label.setText)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)

    def showEvent(self, event):
        super().showEvent(event)
        self._thread.start()

    def closeEvent(self, event):
        if self._thread.isRunning():
            self.action_btn.setEnabled(False)
            self._worker.request_cancel()
            event.ignore()
            return
        super().closeEvent(event)

    def _on_progress(self, value: int):
        if value < 0:
            self.progress_bar.setRange(0, 0)
            self.percent_label.setText("--")
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(value)
            self.percent_label.setText(f"{value}%")

    def _on_finished(self, success: bool, error_msg: str):
        self._thread.quit()
        self._thread.wait()
        self._success = success
        self.action_btn.setEnabled(True)
        if success:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
            self.percent_label.setText("100%")
            self.title_label.setText("Ready to go!")
            self.subtitle_label.setText("All necessary files have been configured.")
            self.action_btn.setText("Continue")
            self.action_btn.setStyleSheet("background-color: #0ea5e9; color: white;")
        else:
            self.title_label.setText("Setup Failed")
            self.title_label.setStyleSheet("color: #ef4444;")
            self.subtitle_label.setText(error_msg)
            self.action_btn.setText("Close")

    @property
    def setup_succeeded(self):
        return self._success
