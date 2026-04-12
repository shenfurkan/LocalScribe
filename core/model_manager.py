"""
core/model_manager.py

A thread-safe singleton that loads the WhisperModel exactly once and
reuses it for every subsequent transcription. Loading a 3 GB model
on every call would make the app unusable.
"""
import os
import sys
import ctypes
import logging
import threading

from faster_whisper import WhisperModel

# ── Module-level state ────────────────────────────────────────────────────────
_model: WhisperModel | None = None
_model_lock = threading.Lock()
_model_error: str | None = None
_cuda_paths_injected = False   # guard: only inject DLL paths once


# ── CUDA DLL helpers ──────────────────────────────────────────────────────────

def _inject_cuda_paths_once() -> None:
    """
    Adds NVIDIA pip-package DLL directories to the Windows DLL search path.
    Called at most once per process lifetime.
    """
    global _cuda_paths_injected
    if _cuda_paths_injected or os.name != "nt":
        return

    site_packages = next((p for p in sys.path if "site-packages" in p), None)
    if site_packages:
        for pkg in ("cublas", "cudnn"):
            bin_path = os.path.join(site_packages, "nvidia", pkg, "bin")
            if os.path.exists(bin_path):
                os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(bin_path)

    _cuda_paths_injected = True


def _has_cuda() -> bool:
    """Returns True if CUDA runtime DLLs are accessible on this machine."""
    if os.name != "nt":
        return True  # assume CUDA available on non-Windows (Linux/macOS)
    try:
        ctypes.windll.LoadLibrary("cublas64_12.dll")
        return True
    except OSError:
        return False


def _resolve_download_root(download_root: str | None) -> str:
    if download_root is not None:
        return download_root
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "models")
    # Running as a plain Python script: two levels up from this file → project root
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")


# ── Public API ────────────────────────────────────────────────────────────────

def is_model_loaded() -> bool:
    return _model is not None


def get_model_error() -> str | None:
    return _model_error


def get_model(download_root: str | None = None) -> WhisperModel:
    """
    Returns the cached WhisperModel, loading it first if needed.
    Thread-safe: the lock ensures only one thread loads the model
    even if multiple threads call this simultaneously.

    On GPU machines  → float16 precision, CUDA device
    On CPU machines  → int8 precision (4× faster than float32)
    """
    global _model, _model_error

    # Inject CUDA DLL dirs once before anything else tries to load them.
    _inject_cuda_paths_once()

    if _model is None:
        with _model_lock:
            if _model is None:   # double-checked locking
                try:
                    root = _resolve_download_root(download_root)
                    local_model_path = os.path.join(root, "large-v3-local")

                    if os.path.exists(local_model_path) and os.path.exists(
                        os.path.join(local_model_path, "model.bin")
                    ):
                        model_path = local_model_path
                        logging.info("Loading faster-whisper large-v3 from local path: %s", model_path)
                    else:
                        model_path = "large-v3"
                        logging.info("Loading faster-whisper large-v3 (will download to %s)…", root)

                    if _has_cuda():
                        run_device, run_compute = "auto", "auto"
                    else:
                        logging.warning(
                            "CUDA DLLs not found — forcing CPU/int8 mode. "
                            "GPU acceleration will be unavailable."
                        )
                        run_device, run_compute = "cpu", "int8"

                    _model = WhisperModel(
                        model_path,
                        device=run_device,
                        compute_type=run_compute,
                        cpu_threads=4,
                        download_root=root,
                    )
                    _model_error = None
                    logging.info("Whisper model loaded successfully.")
                except Exception as exc:
                    _model_error = str(exc)
                    logging.error("Failed to load model: %s", exc, exc_info=True)
                    raise

    return _model


def unload_model() -> None:
    """Release the model from memory (useful for testing / cleanup)."""
    global _model, _model_error
    with _model_lock:
        _model = None
        _model_error = None
