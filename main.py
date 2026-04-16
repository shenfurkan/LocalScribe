"""main.py — Application entry-point for LocalScribe.

Startup sequence
----------------
1. Resolve the project root (exe dir when frozen, script dir in dev).
2. Add root to sys.path and set it as the working directory so that
   relative asset paths (QSS themes, icons) always resolve correctly.
3. Create the QApplication and apply default font / icon.
4. **First-run gate** — call ``is_model_ready()`` (checks that the
   Whisper model.bin exists on disk *and* exceeds 1 GB).  If the model
   is missing or incomplete, a blocking ``SetupDialog`` is shown that
   downloads the model from Hugging Face Hub.
5. After setup completes, a **double-check** verifies the model once
   more.  If it is still absent the app shows a critical dialog and
   exits — this prevents a broken main window from appearing.
6. Launch ``MainWindow``, which pre-loads the model into RAM in a
   background thread so the first transcription starts instantly.

The ``is_model_ready()`` call is wrapped in ``try / except`` so that
if the manifest file or the paths module fails (e.g. missing asset in
a PyInstaller bundle), the app falls back to showing the setup dialog
instead of crashing silently.
"""

import sys
import os
import logging

# ---------------------------------------------------------------------------
# Path bootstrapping — must happen *before* any project imports.
# In a PyInstaller --onedir bundle ``sys.frozen`` is ``True`` and the
# executable lives next to the ``_internal/`` data directory.  In dev
# mode the script's own directory is the project root.
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    _ROOT = os.path.dirname(sys.executable)
else:
    _ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Set the working directory so relative paths like "assets/dark_theme.qss"
# or "image/LocalScribe.ico" resolve regardless of how the app was launched.
os.chdir(_ROOT)

# ---------------------------------------------------------------------------
# PyInstaller --windowed sets sys.stdout and sys.stderr to None because
# there is no console attached.  Several libraries (tqdm, huggingface_hub)
# call sys.stderr.write() unconditionally and crash with:
#   AttributeError: 'NoneType' object has no attribute 'write'
#
# Fix: replace None streams with a no-op file-like object so that any
# library that tries to write to them silently succeeds instead of crashing.
# This must be done before any library imports, which is why it lives here.
# ---------------------------------------------------------------------------
class _NullStream:
    """Minimal file-like object that silently discards all output."""
    def write(self, *_): pass
    def flush(self): pass
    def fileno(self): raise OSError("NullStream has no file descriptor")

if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()

# Hugging Face Hub prints a noisy warning about symlinks on Windows NTFS.
# The flag below silences it — we never rely on symlinks anyway.
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# Disable tqdm progress bars inside hf_hub_download entirely.
# We implement our own per-file progress reporting via Qt signals,
# so tqdm's bars would appear as garbage in the log view anyway.
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

# Enable Xet-backed download acceleration when available.
# huggingface_hub >= 0.32 can use hf_xet for faster chunk-based downloads.
# If hf_xet is not installed, huggingface_hub transparently falls back.
os.environ.setdefault("HF_HUB_ENABLE_HF_XET", "1")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QIcon
from core.setup_manager import is_model_ready
from ui.setup_dialog import SetupDialog
from ui.main_window import MainWindow


def main():
    """Entry-point: create the Qt app, gate on model readiness, launch UI."""

    app = QApplication(sys.argv)
    app.setApplicationName("LocalScribe")
    app.setOrganizationName("LocalScribe")

    # Apply the native Windows "Segoe UI" font for a polished look.
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setWindowIcon(QIcon(os.path.join(_ROOT, "image", "LocalScribe.ico")))

    # ------------------------------------------------------------------
    # FIRST-RUN GATE — ensure the Whisper model is present on disk.
    # is_model_ready() reads core/runtime_manifest.json, resolves the
    # expected model path via core/paths.models_dir(), and checks that
    # the binary exists and is >= 1 GB (guards against partial downloads).
    #
    # The try/except guarantees that ANY failure (missing manifest, bad
    # path, permission error, etc.) falls through to the setup dialog
    # rather than crashing the app.
    # ------------------------------------------------------------------
    try:
        model_ready = is_model_ready()
    except Exception as exc:
        logging.warning("is_model_ready() failed — forcing setup: %s", exc)
        model_ready = False

    if not model_ready:
        # Show the blocking setup dialog that downloads the model.
        # SetupDialog.exec() returns only when the user clicks Continue
        # (success) or Close (failure / cancel).
        dlg = SetupDialog()
        dlg.exec()
        if not dlg.setup_succeeded:
            sys.exit(1)  # user cancelled or download failed

    # DOUBLE-CHECK — if the model is STILL not ready after the dialog,
    # something went wrong (e.g. disk full, antivirus quarantine).  Show
    # a clear error instead of letting the main window open broken.
    try:
        if not is_model_ready():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None,
                "Model Missing",
                "Whisper model is not available.\n\n"
                "Please restart LocalScribe and complete the first-time setup.",
            )
            sys.exit(1)
    except Exception:
        # If even the double-check crashes, proceed anyway — the model
        # manager's get_model() will raise a clear RuntimeError later.
        pass

    # ------------------------------------------------------------------
    # LAUNCH — MainWindow starts a background thread to pre-load the
    # model into RAM so the first transcription starts instantly.
    # ------------------------------------------------------------------
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
