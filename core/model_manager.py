"""
core/model_manager.py — Thread-safe singleton for the Whisper model.

The faster-whisper ``WhisperModel`` object holds the full 3 GB model
in RAM (or VRAM).  Creating it takes several seconds, so we load it
exactly once and reuse it for every transcription request.

Thread safety
-------------
A ``threading.Lock`` with **double-checked locking** guarantees that
even if two QThreads call ``get_model()`` simultaneously, only one
thread will actually instantiate the model.

CUDA path injection (Windows-specific)
--------------------------------------
NVIDIA ships CUDA runtime DLLs inside pip packages (e.g.
``nvidia-cublas-cu12``).  On Windows, Python ≥ 3.8 requires an
explicit call to ``os.add_dll_directory()`` before these DLLs can be
found.  ``_inject_cuda_paths_once()`` scans known locations and
registers them once per process lifetime.

Fail-fast design
-----------------
``get_model()`` **never** silently downloads the model.  If the local
binary is missing or incomplete it raises ``RuntimeError`` immediately
with an actionable message, forcing the user back through the setup
dialog.
"""
import os
import sys
import ctypes
import logging
import threading
from pathlib import Path

from faster_whisper import WhisperModel
from core.paths import models_dir

# ── Module-level state ──────────────────────────────────────────────────────────
_model: WhisperModel | None = None       # cached singleton; set once
_model_lock = threading.Lock()            # serialises first-load attempts
_model_error: str | None = None           # last error message (for UI)
_cuda_paths_injected = False              # guard: only inject DLL paths once per process
_cuda_dll_dir_handles: list = []          # prevent GC of os.add_dll_directory handles

# Minimum acceptable size (bytes) for the model binary.
# The real model.bin is ~3 GB; 1 GB rejects partial / interrupted downloads.
_MIN_MODEL_BIN_BYTES = 1_000_000_000


# ── CUDA DLL helpers ──────────────────────────────────────────────────────────

def _inject_cuda_paths_once() -> None:
    """Register NVIDIA pip-package DLL directories with the Windows loader.

    Since Python 3.8 on Windows, DLLs are no longer found via ``%PATH%``
    alone — they must be registered with ``os.add_dll_directory()``.
    We also prepend to ``%PATH%`` for ctypes and subprocess compatibility.

    Called at most once per process lifetime (guarded by
    ``_cuda_paths_injected``).  On non-Windows systems this is a no-op.
    """
    global _cuda_paths_injected
    if _cuda_paths_injected or os.name != "nt":
        return

    # Gather all plausible site-packages directories.  In a PyInstaller
    # bundle the DLLs may live under  _internal/  next to the exe.
    candidate_roots = list(dict.fromkeys(
        [
            p for p in sys.path
            if isinstance(p, str)
            and ("site-packages" in p or "dist-packages" in p)
        ] + [
            os.path.join(sys.prefix, "Lib", "site-packages"),
            os.path.join(sys.base_prefix, "Lib", "site-packages"),
            os.path.join(os.path.dirname(sys.executable), "_internal"),
            os.path.dirname(sys.executable),
        ]
    ))

    # NVIDIA ships each CUDA library in its own pip package with a
    # ``bin/`` subfolder containing the actual DLLs.
    candidate_bins: list[str] = []
    nvidia_pkgs = (
        "cublas",
        "cudnn",
        "cuda_runtime",
        "cuda_nvrtc",
        "cufft",
        "curand",
        "cusolver",
        "cusparse",
    )

    for root in candidate_roots:
        for pkg in nvidia_pkgs:
            bin_path = os.path.join(root, "nvidia", pkg, "bin")
            if os.path.isdir(bin_path):
                candidate_bins.append(bin_path)

    # De-duplicate while preserving discovery order.
    candidate_bins = list(dict.fromkeys(candidate_bins))
    if candidate_bins:
        # Prepend to %PATH% for ctypes.WinDLL and subprocess compatibility.
        os.environ["PATH"] = os.pathsep.join(candidate_bins) + os.pathsep + os.environ.get("PATH", "")
        # os.add_dll_directory() is the official Python >= 3.8 mechanism
        # for extending the DLL search path on Windows.
        # Ref: https://docs.python.org/3/library/os.html#os.add_dll_directory
        if hasattr(os, "add_dll_directory"):
            for bin_path in candidate_bins:
                try:
                    handle = os.add_dll_directory(bin_path)
                    _cuda_dll_dir_handles.append(handle)  # prevent GC
                except OSError:
                    pass
        logging.info("Injected %s CUDA DLL directories.", len(candidate_bins))

    _cuda_paths_injected = True


def _has_cuda() -> bool:
    """Probe whether CUDA runtime DLLs are loadable.

    On Windows we attempt to load ``cublas64_*.dll`` via ``ctypes.WinDLL``.
    If the DLL loads successfully, CUDA is available.  On Linux / macOS we
    optimistically return True and let CTranslate2 handle the fallback.
    """
    if os.name != "nt":
        return True

    # cuBLAS is a reliable indicator — if it loads, CUDA is functional.
    cublas_candidates = (
        "cublas64_12.dll",   # CUDA 12.x
        "cublas64_11.dll",   # CUDA 11.x
    )

    for dll_name in cublas_candidates:
        try:
            ctypes.WinDLL(dll_name)
            return True
        except OSError:
            continue

    return False


def _resolve_download_root(download_root: str | None) -> str:
    """Return the directory that contains downloaded model folders.

    Defaults to ``core.paths.models_dir()`` which is:
    * Frozen: ``%LOCALAPPDATA%\\LocalScribe\\models``
    * Dev:    ``<project_root>/models``
    """
    if download_root is not None:
        return download_root
    return str(models_dir())


def _local_model_bin_path(root: str) -> Path:
    """Expected path to the main model binary.

    The setup manager downloads the Hugging Face repo into a folder
    called ``large-v3-local`` under the models root.  Inside that
    folder, ``model.bin`` is the CTranslate2-format model file that
    faster-whisper loads.
    """
    return Path(root) / "large-v3-local" / "model.bin"


def _is_local_model_complete(root: str) -> bool:
    """Check that model.bin exists and exceeds the minimum size threshold."""
    model_bin = _local_model_bin_path(root)
    if not model_bin.exists():
        return False
    try:
        return model_bin.stat().st_size >= _MIN_MODEL_BIN_BYTES
    except OSError:
        return False


def _recommended_cpu_threads() -> int:
    """Pick a practical CPU thread count for faster-whisper."""
    cores = os.cpu_count() or 4
    if cores <= 4:
        return max(1, cores)
    return min(8, cores - 1)


# ── Public API ────────────────────────────────────────────────────────────────

def is_model_loaded() -> bool:
    return _model is not None


def get_model_error() -> str | None:
    return _model_error


def get_model(download_root: str | None = None) -> WhisperModel:
    """Return the singleton ``WhisperModel``, loading it on first call.

    Thread safety
    ~~~~~~~~~~~~~
    Uses double-checked locking with ``_model_lock`` so that even if
    two worker threads call ``get_model()`` concurrently, only one
    will actually instantiate the model.

    Device selection
    ~~~~~~~~~~~~~~~~
    * **CUDA available** → ``device='auto', compute_type='auto'``
      (CTranslate2 picks the best GPU precision automatically).
    * **CPU only** → ``device='cpu', compute_type='int8'``
      (int8 is ~4× faster than float32 on modern x86 CPUs).

    Raises
    ------
    RuntimeError
        If the local model binary is missing or incomplete.  This
        forces the caller back through the setup dialog instead of
        silently attempting a multi-GB download.
    """
    global _model, _model_error

    # Inject CUDA DLL dirs once before anything else tries to load them.
    _inject_cuda_paths_once()

    if _model is None:
        with _model_lock:
            if _model is None:   # double-checked locking pattern
                try:
                    root = _resolve_download_root(download_root)
                    local_model_path = os.path.join(root, "large-v3-local")
                    os.makedirs(root, exist_ok=True)

                    # FAIL-FAST: refuse to proceed if the model binary
                    # is absent or incomplete.  This prevents the old
                    # behaviour where faster-whisper would silently
                    # start a multi-GB download with no UI feedback.
                    if _is_local_model_complete(root):
                        model_path = local_model_path
                        logging.info("Loading faster-whisper large-v3 from local path: %s", model_path)
                    else:
                        model_bin = _local_model_bin_path(root)
                        raise RuntimeError(
                            "Whisper model is missing or incomplete. "
                            "Run first-time setup to download it before starting transcription. "
                            f"Expected file: {model_bin}"
                        )

                    # Select device and precision based on CUDA availability.
                    if _has_cuda():
                        run_device, run_compute = "auto", "auto"
                    else:
                        logging.warning(
                            "CUDA DLLs not found - forcing CPU/int8 mode. "
                            "GPU acceleration will be unavailable."
                        )
                        run_device, run_compute = "cpu", "int8"

                    cpu_threads = _recommended_cpu_threads()
                    logging.info("Using cpu_threads=%s for faster-whisper.", cpu_threads)

                    # WhisperModel first arg is model_size_or_path.
                    # When given a local directory path it loads from
                    # disk instead of downloading from Hugging Face.
                    # Ref: https://github.com/SYSTRAN/faster-whisper
                    _model = WhisperModel(
                        model_path,
                        device=run_device,
                        compute_type=run_compute,
                        cpu_threads=cpu_threads,
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
