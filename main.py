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
def setup_logging():
    """Configure logging with appropriate handlers for dev vs compiled builds."""
    log_level = logging.INFO
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Clear any existing handlers
    logging.getLogger().handlers.clear()
    
    # Create formatter
    formatter = logging.Formatter(log_format)
    
    # Console handler (dev only). In PyInstaller --windowed mode, stdout/stderr
    # may be None, so avoid attaching a console stream there.
    if not getattr(sys, 'frozen', False):
        console_handler = logging.StreamHandler(sys.stderr if sys.stderr is not None else sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        logging.getLogger().addHandler(console_handler)
    
    # File handler for compiled builds (write to user data directory)
    if getattr(sys, 'frozen', False):
        try:
            from core.paths import data_root
            log_file = data_root() / "LocalScribe.log"
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            logging.getLogger().addHandler(file_handler)
            logging.info("Logging to file: %s", log_file)
        except Exception as e:
            # If we can't set up file logging, at least log to console
            logging.warning("Could not set up file logging: %s", e)
    
    logging.getLogger().setLevel(log_level)
    logging.info("Logging configured. Frozen: %s, Root: %s", getattr(sys, 'frozen', False), _ROOT)

# Add the project root to sys.path so imports work from any entry point.
_ROOT = os.path.abspath(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Setup logging early (after _ROOT is defined)
setup_logging()

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

from PySide6.QtWidgets import QApplication, QSplashScreen
from PySide6.QtGui import QFont, QIcon, QPixmap, QColor, QPainter
from PySide6.QtCore import Qt


def _make_splash(app):
    """Create and show a minimal splash screen using only Qt primitives.
    This appears immediately before any heavy ML imports begin."""
    w, h = 480, 200
    px = QPixmap(w, h)
    px.fill(QColor("#0f172a"))
    painter = QPainter(px)
    painter.setPen(QColor("#f8fafc"))
    font_title = QFont("Segoe UI", 22, QFont.Bold)
    painter.setFont(font_title)
    painter.drawText(0, 0, w, h - 40, Qt.AlignCenter, "LocalScribe")
    painter.setPen(QColor("#0ea5e9"))
    painter.drawRect(80, h // 2 + 10, w - 160, 2)
    painter.setPen(QColor("#94a3b8"))
    font_sub = QFont("Segoe UI", 10)
    painter.setFont(font_sub)
    painter.drawText(0, h // 2 + 22, w, 30, Qt.AlignCenter, "Loading AI Engine...")
    painter.end()
    splash = QSplashScreen(px, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
    splash.show()
    app.processEvents()
    return splash


def main():
    """Entry-point: create the Qt app, gate on model readiness, launch UI."""

    app = QApplication(sys.argv)
    app.setApplicationName("LocalScribe")
    app.setOrganizationName("LocalScribe")

    font = QFont("Segoe UI", 10)
    app.setFont(font)
    from core.paths import app_bundle_dir
    icon_path = os.path.join(str(app_bundle_dir()), "image", "LocalScribe.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
        logging.info("Application icon loaded from: %s", icon_path)
    else:
        logging.warning("Application icon not found at: %s", icon_path)

    # Show splash immediately — before any heavy ML imports below.
    splash = _make_splash(app)

    # Heavy imports deferred to here so the splash renders first.
    from core.setup_manager import (
        adopt_legacy_model_if_needed,
        find_ready_model_id,
        get_default_model_id,
        get_active_model_id,
        is_model_ready,
        set_active_model_id,
    )
    from ui.setup_dialog import SetupDialog
    from ui.main_window import MainWindow

    # ------------------------------------------------------------------
    # FIRST-RUN GATE — ensure a model is selected AND present on disk.
    #
    # We show the setup wizard (token → picker → download) whenever:
    #   * no active model id has been persisted in setup_state.json, or
    #   * the selected model is missing / incomplete on disk.
    #
    # This guarantees fresh installs always see the model picker, even
    # if a previous models/ folder is present. Users can then click
    # "Use this model" on an already-installed model without paying
    # for a re-download.
    #
    # The try/except guarantees that ANY failure (missing manifest, bad
    # path, permission error, etc.) falls through to the setup dialog
    # rather than crashing the app.
    # ------------------------------------------------------------------
    try:
        active = get_active_model_id()

        # If no active model is recorded yet, try to adopt a previously-downloaded
        # model from legacy/local folders and activate any ready model found.
        if not active:
            adopt_legacy_model_if_needed(get_default_model_id())
            ready = find_ready_model_id()
            if ready:
                set_active_model_id(ready)
                active = ready

        # If an active id exists but is missing in the runtime model store,
        # attempt legacy-path adoption once before showing setup.
        if active and not is_model_ready(active):
            adopt_legacy_model_if_needed(active)

        needs_setup = (not active) or (not is_model_ready(active))
    except Exception as exc:
        logging.warning("Setup gate check failed — forcing setup: %s", exc)
        needs_setup = True

    if needs_setup:
        # Hide the splash screen so it never overlaps the setup wizard.
        splash.hide()
        splash.close()
        splash._already_closed = True
        app.processEvents()

        dlg = SetupDialog()
        dlg.exec()
        if not dlg.setup_succeeded:
            sys.exit(1)  # user cancelled or download failed

    # DOUBLE-CHECK — if the model is STILL not ready after the dialog,
    # something went wrong (e.g. disk full, antivirus quarantine).  Show
    # a clear error instead of letting the main window open broken.
    try:
        active_model = get_active_model_id()
        if not active_model or not is_model_ready(active_model):
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
    # LAUNCH — close splash, show MainWindow.
    # ------------------------------------------------------------------
    window = MainWindow()
    window.setWindowIcon(app.windowIcon())
    window.show()
    if not getattr(splash, '_already_closed', False):
        splash.finish(window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
