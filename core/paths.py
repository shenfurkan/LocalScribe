"""
core/paths.py — Single source of truth for every data directory.

Why this file exists
--------------------
LocalScribe stores large ML models (~3 GB) and user-generated transcripts
on disk.  The location must be **user-writable** even when the application
is installed into a read-only folder like ``C:\\Program Files``.

Strategy
--------
- **Frozen (PyInstaller exe):**
  Uses the OS-standard per-user data directory so that each Windows user
  gets their own copy and the folder survives app updates / reinstalls.

  ==========  ============================================
  Platform    Path
  ==========  ============================================
  Windows     ``%LOCALAPPDATA%\\LocalScribe``
  macOS       ``~/Library/Application Support/LocalScribe``
  Linux       ``$XDG_DATA_HOME/LocalScribe`` (default ``~/.local/share``)
  ==========  ============================================

- **Development mode (plain Python):**
  Data directories live under the project root so that ``models/`` and
  ``transcripts/`` are visible right next to the source code.

All public helpers create their target directory on first call
(``mkdir(parents=True, exist_ok=True)``) so callers never need to
worry about missing folders.
"""

import os
import sys
from pathlib import Path

# Application name used as the directory leaf for per-user data paths.
_APP_NAME = "LocalScribe"


def _is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def _user_data_root() -> Path:
    """Return the OS-appropriate per-user data directory.

    On Windows this is typically::

        C:\\Users\\<user>\\AppData\\Local\\LocalScribe

    The Inno Setup installer explicitly marks this directory as
    ``uninsneveruninstall`` so that models and transcripts survive
    application updates.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / _APP_NAME
        # Fallback for rare Windows configurations without LOCALAPPDATA.
        return Path.home() / "AppData" / "Local" / _APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_NAME

    # Linux / other POSIX — respect $XDG_DATA_HOME if set.
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / _APP_NAME
    return Path.home() / ".local" / "share" / _APP_NAME


def _project_root() -> Path:
    """Return the project root when running from source."""
    return Path(__file__).resolve().parent.parent


# ── Public API ────────────────────────────────────────────────────────────────

def data_root() -> Path:
    """Top-level data directory (per-user for frozen builds, project root for dev)."""
    if _is_frozen():
        return _user_data_root()
    return _project_root()


def models_dir() -> Path:
    """Directory where Whisper and other ML models are stored.

    Created eagerly on first call so that both setup_manager (which
    downloads into this directory) and model_manager (which reads from
    it) never have to worry about ``FileNotFoundError``.
    """
    d = data_root() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def transcripts_dir() -> Path:
    """Directory where transcript JSON files are stored."""
    d = data_root() / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_bundle_dir() -> Path:
    """Directory containing bundled static assets (icons, QSS themes).

    **Read-only** — never write user data here.

    PyInstaller ``--onedir`` places data files that were added via
    ``--add-data`` in an ``_internal/`` subfolder next to the ``.exe``.
    We check for that folder first; if it doesn't exist we fall back
    to the project root (development mode).
    """
    if _is_frozen():
        exe_dir = Path(sys.executable).parent
        internal = exe_dir / "_internal"
        return internal if internal.exists() else exe_dir
    return _project_root()
