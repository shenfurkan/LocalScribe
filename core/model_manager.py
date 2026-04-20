"""core/model_manager.py — Thread-safe singleton for the Whisper model.

The faster-whisper ``WhisperModel`` object holds the full 3 GB model
in RAM (or VRAM).  Creating it takes several seconds, so we load it
exactly once and reuse it for every transcription request.

Thread safety
-------------
A ``threading.Lock`` with **double-checked locking** guarantees that
even if two QThreads call ``get_model()`` simultaneously, only one
thread will actually instantiate the model.

GPU acceleration
----------------
Hardware detection and CUDA environment configuration are delegated to
``core.gpu_manager``.  That module probes CTranslate2's built-in CUDA
runtime first (most reliable), falls back to nvidia-smi, then to DLL
loading.  ``gpu_manager.ensure_cuda_env()`` registers all necessary
DLL directories so that PyInstaller-bundled builds find cuBLAS / cuDNN
at runtime.

Fail-fast design
-----------------
``get_model()`` **never** silently downloads the model.  If the local
binary is missing or incomplete it raises ``RuntimeError`` immediately
with an actionable message, forcing the user back through the setup
dialog.
"""
import os
import logging
import threading
from pathlib import Path

from faster_whisper import WhisperModel
from core.paths import models_dir
from core.gpu_manager import detect_gpu, ensure_cuda_env, optimal_compute_type, optimal_cpu_threads
from core.setup_manager import (
    get_active_model_id,
    get_default_model_id,
    get_model_entry,
)

# ── Module-level state ──────────────────────────────────────────────────────────
_model: WhisperModel | None = None       # cached singleton; set once
_model_lock = threading.Lock()            # serialises first-load attempts
_model_error: str | None = None           # last error message (for UI)
logger = logging.getLogger(__name__)

# Fallback minimum acceptable size (bytes) for the model binary when the
# active model's manifest entry does not declare one.
_MIN_MODEL_BIN_BYTES = 1_000_000_000


def _active_model_entry() -> dict:
    """Return the manifest entry for the currently active (or default) model."""
    model_id = get_active_model_id() or get_default_model_id()
    entry = get_model_entry(model_id) or {}
    return entry



def _resolve_download_root(download_root: str | None) -> str:
    """Return the directory that contains downloaded model folders.

    Defaults to ``core.paths.models_dir()`` which is:
    * Frozen: ``%LOCALAPPDATA%\\LocalScribe\\models``
    * Dev:    ``<project_root>/models``
    """
    if download_root is not None:
        return download_root
    return str(models_dir())


def _local_model_folder(root: str) -> Path:
    """Return the folder containing the active model's files.

    Custom/user-imported entries carry an absolute ``abs_path`` and live
    outside ``models_dir()``. All other entries are stored inside
    ``models_dir()/<local_dir_name>``.
    """
    entry = _active_model_entry()
    abs_path = entry.get("abs_path")
    if abs_path:
        return Path(abs_path)
    folder = entry.get("local_dir_name", "large-v3-local")
    return Path(root) / folder


def _local_model_bin_path(root: str) -> Path:
    """Expected path to the active model's binary on disk."""
    entry = _active_model_entry()
    filename = entry.get("expected_file", "model.bin")
    return _local_model_folder(root) / filename


def _is_local_model_complete(root: str) -> bool:
    """Check that the active model's binary exists and is large enough."""
    model_bin = _local_model_bin_path(root)
    if not model_bin.exists():
        return False
    entry = _active_model_entry()
    min_bytes = int(entry.get("min_bin_size_bytes", _MIN_MODEL_BIN_BYTES))
    try:
        return model_bin.stat().st_size >= min_bytes
    except OSError:
        return False


def _looks_like_cuda_runtime_error(exc: Exception) -> bool:
    """Best-effort detection of CUDA/DLL runtime failures.

    Used to decide when we should gracefully retry model initialization on CPU
    instead of failing hard.
    """
    text = str(exc).lower()
    needles = (
        "dll",
        "cublas",
        "cudnn",
        "cuda",
        "ctranslate2",
        "load library",
        "could not load",
        "error 126",
        "error 127",
    )
    return any(n in text for n in needles)


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
    Delegated to ``core.gpu_manager.optimal_compute_type()`` which
    picks the best device and precision for the detected hardware:

    * **CUDA + ≥ 8 GB VRAM** → ``("cuda", "float16")``
    * **CUDA + 4–8 GB VRAM** → ``("cuda", "int8_float16")``
    * **CUDA + < 4 GB VRAM** → ``("cuda", "int8")``
    * **CPU only** → ``("cpu", "int8")``

    Raises
    ------
    RuntimeError
        If the local model binary is missing or incomplete.  This
        forces the caller back through the setup dialog instead of
        silently attempting a multi-GB download.
    """
    global _model, _model_error

    # Configure CUDA DLL paths before anything tries to load them.
    logger.info("Configuring CUDA environment before model loading...")
    ensure_cuda_env()
    logger.info("CUDA environment configured successfully.")

    if _model is None:
        with _model_lock:
            if _model is None:   # double-checked locking pattern
                try:
                    root = _resolve_download_root(download_root)
                    entry = _active_model_entry()
                    local_model_path = str(_local_model_folder(root))
                    os.makedirs(root, exist_ok=True)

                    # FAIL-FAST: refuse to proceed if the model binary
                    # is absent or incomplete.  This prevents the old
                    # behaviour where faster-whisper would silently
                    # start a multi-GB download with no UI feedback.
                    if _is_local_model_complete(root):
                        model_path = local_model_path
                        logging.info(
                            "Loading faster-whisper model '%s' from: %s",
                            entry.get("id", "?"), model_path,
                        )
                    else:
                        model_bin = _local_model_bin_path(root)
                        raise RuntimeError(
                            "Whisper model is missing or incomplete. "
                            "Run first-time setup to download it before starting transcription. "
                            f"Expected file: {model_bin}"
                        )

                    # ── GPU detection and compute-type selection ───────
                    gpu_info = detect_gpu()
                    logging.info("Hardware: %s", gpu_info.summary())
                    run_device, run_compute = optimal_compute_type(gpu_info)
                    logging.info(
                        "Model config: device=%s, compute_type=%s",
                        run_device, run_compute,
                    )

                    cpu_threads = optimal_cpu_threads()
                    logging.info("Using cpu_threads=%s for faster-whisper.", cpu_threads)

                    # WhisperModel first arg is model_size_or_path.
                    # When given a local directory path it loads from
                    # disk instead of downloading from Hugging Face.
                    # Ref: https://github.com/SYSTRAN/faster-whisper
                    # ── Performance tuning ────────────────────────
                    # num_workers: parallel decoding threads for beam search.
                    #   On GPU: 1 is optimal (GPU handles parallelism).
                    #   On CPU: match cpu_threads for throughput.
                    num_workers = 1 if run_device == "cuda" else cpu_threads

                    try:
                        _model = WhisperModel(
                            model_path,
                            device=run_device,
                            compute_type=run_compute,
                            cpu_threads=cpu_threads,
                            num_workers=num_workers,
                            download_root=root,
                        )
                    except Exception as exc:
                        if run_device == "cuda" and _looks_like_cuda_runtime_error(exc):
                            logger.warning(
                                "CUDA initialization failed (%s). Falling back to CPU int8.",
                                exc,
                            )
                            run_device = "cpu"
                            run_compute = "int8"
                            num_workers = cpu_threads
                            _model = WhisperModel(
                                model_path,
                                device=run_device,
                                compute_type=run_compute,
                                cpu_threads=cpu_threads,
                                num_workers=num_workers,
                                download_root=root,
                            )
                        else:
                            raise
                    _model_error = None
                    logging.info(
                        "Whisper model loaded successfully on %s (%s).",
                        run_device, run_compute,
                    )
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
