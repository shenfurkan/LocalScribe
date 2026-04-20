"""ui/dialogs/cuda_setup_dialog.py — GPU Acceleration settings dialog.

Displays the current GPU and CUDA library status, and lets the user
install or remove the NVIDIA libraries needed for GPU-accelerated
transcription directly from within the application.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame, QMessageBox,
)
from PySide6.QtCore import Qt, QThread

from core.gpu_manager import detect_gpu, reset_cuda_env
from core.cuda_installer import cuda_lib_status, CudaInstallWorker, uninstall_cuda_libs


class CudaSetupDialog(QDialog):
    """Settings dialog for managing GPU acceleration and CUDA libraries."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GPU Acceleration")
        self.setFixedWidth(480)
        self.setModal(True)

        self._active_thread = None
        self._active_worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # ── GPU Hardware ──────────────────────────────────────────────
        hw_header = QLabel("Hardware")
        hw_header.setObjectName("FieldLabel")
        layout.addWidget(hw_header)

        gpu_info = detect_gpu(force_refresh=True)

        self.gpu_name_label = QLabel()
        self.gpu_name_label.setObjectName("SentenceDesc")
        self.gpu_name_label.setWordWrap(True)
        layout.addWidget(self.gpu_name_label)

        if gpu_info.cuda_available:
            self.gpu_name_label.setText(
                f"<b>{gpu_info.device_name}</b> — {gpu_info.vram_total_mb} MB VRAM<br>"
                f"Driver: {gpu_info.driver_version or 'N/A'} &nbsp;·&nbsp; "
                f"Detection: {gpu_info.detection_method}"
            )
        elif gpu_info.device_name:
            self.gpu_name_label.setText(
                f"<b>{gpu_info.device_name}</b> detected, but CUDA libraries "
                "are not installed.  Install them below to enable "
                "GPU-accelerated transcription."
            )
        else:
            # Try nvidia-smi directly for hardware info even without CUDA
            from core.gpu_manager import _nvidia_smi_query
            name, driver, vram = _nvidia_smi_query()
            if name:
                self.gpu_name_label.setText(
                    f"<b>{name}</b> — {vram} MB VRAM<br>"
                    f"Driver: {driver or 'N/A'}<br>"
                    "CUDA libraries are not installed. Install them below "
                    "to enable GPU acceleration."
                )
            else:
                self.gpu_name_label.setText(
                    "No NVIDIA GPU detected. GPU acceleration is not "
                    "available on this system. Transcription will use CPU."
                )

        # Separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setObjectName("SectionSeparator")
        layout.addWidget(sep1)

        # ── CUDA Libraries ────────────────────────────────────────────
        libs_header = QLabel("CUDA Libraries")
        libs_header.setObjectName("FieldLabel")
        layout.addWidget(libs_header)

        libs_desc = QLabel(
            "These libraries are required for GPU-accelerated transcription. "
            "They will be downloaded from NVIDIA's PyPI repository (~800 MB total)."
        )
        libs_desc.setObjectName("SentenceDesc")
        libs_desc.setWordWrap(True)
        layout.addWidget(libs_desc)

        # Per-package status labels
        self.pkg_labels: list[QLabel] = []
        status = cuda_lib_status()
        for pkg in status["packages"]:
            icon = "✓" if pkg["installed"] else "✗"
            color = "#22c55e" if pkg["installed"] else "#ef4444"
            lbl = QLabel(f'<span style="color:{color}; font-weight:bold;">{icon}</span>  {pkg["label"]}')
            lbl.setObjectName("SentenceDesc")
            self.pkg_labels.append(lbl)
            layout.addWidget(lbl)

        # ── Progress area (hidden initially) ──────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setObjectName("SentenceDesc")
        self.status_label.hide()
        layout.addWidget(self.status_label)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setObjectName("SectionSeparator")
        layout.addWidget(sep2)

        # ── Buttons ───────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.remove_btn = QPushButton("Remove Libraries")
        self.remove_btn.clicked.connect(self._on_remove)
        self.remove_btn.setEnabled(status["all_installed"])

        self.install_btn = QPushButton("Install CUDA Libraries")
        self.install_btn.setObjectName("PrimaryBtn")
        self.install_btn.clicked.connect(self._on_install)
        self.install_btn.setEnabled(not status["all_installed"])

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel_download)
        self.cancel_btn.hide()

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)

        btn_layout.addWidget(self.remove_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.install_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

    # ── Actions ───────────────────────────────────────────────────────

    def _on_install(self):
        """Start downloading CUDA libraries in a background thread."""
        self.install_btn.hide()
        self.remove_btn.setEnabled(False)
        self.cancel_btn.show()
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_label.show()
        self.status_label.setText("Preparing download…")

        self._active_worker = CudaInstallWorker()
        self._active_thread = QThread(self)
        self._active_worker.moveToThread(self._active_thread)
        self._active_thread.started.connect(self._active_worker.run)

        self._active_worker.progress.connect(self._on_progress)
        self._active_worker.status.connect(self._on_status)
        self._active_worker.finished.connect(self._on_install_finished)
        self._active_worker.error.connect(self._on_install_error)

        self._active_worker.finished.connect(self._active_thread.quit)
        self._active_worker.error.connect(self._active_thread.quit)
        self._active_worker.finished.connect(self._active_worker.deleteLater)
        self._active_worker.error.connect(self._active_worker.deleteLater)
        self._active_thread.finished.connect(self._active_thread.deleteLater)

        self._active_thread.start()

    def _on_cancel_download(self):
        if self._active_worker:
            self._active_worker.cancel()
        self._reset_ui()
        self.status_label.setText("Download cancelled.")

    def _on_progress(self, downloaded: int, total: int, package: str):
        if total > 0:
            pct = int(downloaded * 100 / total)
            self.progress_bar.setValue(pct)
            mb_done = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self.status_label.setText(
                f"Downloading {package}…  {mb_done:.0f} / {mb_total:.0f} MB"
            )

    def _on_status(self, msg: str):
        self.status_label.setText(msg)

    def _on_install_finished(self, extracted: list):
        reset_cuda_env()
        self._refresh_status()
        self._reset_ui()
        count = len(extracted)
        self.status_label.setText(
            f"✓ Installation complete — {count} files installed. "
            "Restart LocalScribe to activate GPU acceleration."
        )

    def _on_install_error(self, err: str):
        self._reset_ui()
        self.status_label.setText(f"✗ Installation failed.")
        QMessageBox.critical(
            self, "CUDA Installation Error",
            f"Failed to download CUDA libraries:\n\n{err}\n\n"
            "Check your internet connection and try again."
        )

    def _on_remove(self):
        reply = QMessageBox.question(
            self, "Remove CUDA Libraries",
            "This will remove the downloaded CUDA libraries.\n"
            "GPU acceleration will be disabled until they are reinstalled.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            uninstall_cuda_libs()
            reset_cuda_env()
            self._refresh_status()
            self.status_label.show()
            self.status_label.setText("CUDA libraries removed.")

    # ── UI helpers ────────────────────────────────────────────────────

    def _reset_ui(self):
        self.cancel_btn.hide()
        self.install_btn.show()
        self.progress_bar.hide()
        status = cuda_lib_status()
        self.install_btn.setEnabled(not status["all_installed"])
        self.remove_btn.setEnabled(status["all_installed"])

    def _refresh_status(self):
        status = cuda_lib_status()
        for lbl, pkg in zip(self.pkg_labels, status["packages"]):
            icon = "✓" if pkg["installed"] else "✗"
            color = "#22c55e" if pkg["installed"] else "#ef4444"
            lbl.setText(
                f'<span style="color:{color}; font-weight:bold;">{icon}</span>  {pkg["label"]}'
            )

    def closeEvent(self, event):
        if self._active_worker:
            self._active_worker.cancel()
        if self._active_thread and self._active_thread.isRunning():
            self._active_thread.quit()
            self._active_thread.wait(3000)
        super().closeEvent(event)
