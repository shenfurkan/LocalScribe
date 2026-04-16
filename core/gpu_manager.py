"""
core/gpu_manager.py — Hardware detection and CUDA environment configuration.

This module is the single source of truth for GPU capability detection in
LocalScribe.  It replaces ad-hoc DLL probing with a layered strategy:

Detection layers (in priority order)
-------------------------------------
1. **CTranslate2 runtime query** — ``ctranslate2.get_cuda_device_count()``
   is the most reliable indicator because it tests the exact CUDA runtime
   that faster-whisper will use.  CTranslate2 ≥ 4.x bundles cuBLAS and
   cuDNN inside its own wheel, so no system CUDA toolkit is required.

2. **nvidia-smi probe** — if CTranslate2 is not yet importable (e.g. during
   setup), we shell out to ``nvidia-smi`` to detect NVIDIA hardware and read
   the driver version and VRAM.

3. **DLL load probe** — last-resort check that tries to load well-known
   CUDA DLLs via ``ctypes``.  Useful when CTranslate2 is not installed but
   the CUDA toolkit is.

DLL path injection
------------------
On Windows, Python ≥ 3.8 requires ``os.add_dll_directory()`` for DLLs that
are not on ``%PATH%``.  ``ensure_cuda_env()`` registers the CTranslate2
package directory (which contains ``ctranslate2.dll``, ``cudnn64_9.dll``,
etc.) so that PyInstaller-bundled builds can load them at runtime.

Compute-type selection
----------------------
``optimal_compute_type()`` picks the best precision for the detected GPU:

- **≥ 8 GB VRAM** → ``float16``  (maximum quality)
- **4–8 GB VRAM** → ``int8_float16``  (good quality, fits in VRAM)
- **< 4 GB VRAM** → ``int8``  (fastest, lowest VRAM)
- **CPU fallback** → ``int8``  (4× faster than float32 on modern x86)
"""

import ctypes
import logging
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Module-level state ────────────────────────────────────────────────────────
_gpu_info_cache: Optional["GPUInfo"] = None
_gpu_info_lock = threading.Lock()
_cuda_env_configured = False


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class GPUInfo:
    """Immutable snapshot of the system's GPU capabilities."""
    cuda_available: bool = False
    device_count: int = 0
    device_name: str = ""
    driver_version: str = ""
    vram_total_mb: int = 0
    compute_types: set = field(default_factory=set)
    detection_method: str = "none"

    @property
    def vram_total_gb(self) -> float:
        return self.vram_total_mb / 1024.0

    def summary(self) -> str:
        if not self.cuda_available:
            return "No CUDA-capable GPU detected — using CPU."
        return (
            f"GPU: {self.device_name} | "
            f"VRAM: {self.vram_total_mb} MB | "
            f"Driver: {self.driver_version} | "
            f"Detected via: {self.detection_method}"
        )


# ── Detection layers ─────────────────────────────────────────────────────────

def _detect_via_ctranslate2() -> Optional[GPUInfo]:
    """Layer 1: Use CTranslate2's built-in CUDA runtime query."""
    try:
        import ctranslate2
        count = ctranslate2.get_cuda_device_count()
        if count <= 0:
            return None

        compute_types = set()
        try:
            compute_types = ctranslate2.get_supported_compute_types("cuda")
        except Exception:
            pass

        # Try nvidia-smi for device name and VRAM (supplementary info)
        name, driver, vram = _nvidia_smi_query()

        return GPUInfo(
            cuda_available=True,
            device_count=count,
            device_name=name or "NVIDIA GPU",
            driver_version=driver or "",
            vram_total_mb=vram or 0,
            compute_types=compute_types,
            detection_method="ctranslate2",
        )
    except ImportError:
        return None
    except Exception as exc:
        logger.debug("CTranslate2 CUDA probe failed: %s", exc)
        return None


def _detect_via_nvidia_smi() -> Optional[GPUInfo]:
    """Layer 2: Shell out to nvidia-smi for hardware-level detection."""
    name, driver, vram = _nvidia_smi_query()
    if not name:
        return None

    return GPUInfo(
        cuda_available=True,
        device_count=1,
        device_name=name,
        driver_version=driver or "",
        vram_total_mb=vram or 0,
        compute_types=set(),
        detection_method="nvidia-smi",
    )


def _detect_via_dll_probe() -> Optional[GPUInfo]:
    """Layer 3: Try loading cuBLAS DLLs via ctypes (Windows only)."""
    if os.name != "nt":
        return None

    candidates = ("cublas64_12.dll", "cublas64_11.dll", "nvcuda.dll")
    for dll in candidates:
        try:
            ctypes.WinDLL(dll)
            return GPUInfo(
                cuda_available=True,
                device_count=1,
                device_name="NVIDIA GPU (DLL probe)",
                detection_method="dll_probe",
            )
        except OSError:
            continue
    return None


# ── nvidia-smi helper ─────────────────────────────────────────────────────────

def _nvidia_smi_query() -> tuple[str, str, int]:
    """Query nvidia-smi for GPU name, driver version, and VRAM (MB).

    Returns (name, driver, vram_mb) or ("", "", 0) on failure.
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode != 0:
            return ("", "", 0)

        line = result.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            name = parts[0]
            driver = parts[1]
            vram = int(float(parts[2]))
            return (name, driver, vram)
    except Exception:
        pass
    return ("", "", 0)


# ── CUDA environment configuration ───────────────────────────────────────────

def ensure_cuda_env() -> None:
    """Register CTranslate2 and NVIDIA DLL directories with the Windows loader.

    CTranslate2 ≥ 4.x bundles cuBLAS, cuDNN, and CUDA runtime DLLs inside
    its pip package directory.  In a PyInstaller ``--onedir`` build, these end
    up in ``_internal/ctranslate2/`` next to the exe.  Python ≥ 3.8 on Windows
    requires ``os.add_dll_directory()`` for the OS loader to find them.

    This function also scans for the older ``nvidia/*/bin`` layout used by
    separate ``nvidia-cublas-cu12`` pip packages, as a fallback.

    Safe to call multiple times — guarded by ``_cuda_env_configured``.
    """
    global _cuda_env_configured
    if _cuda_env_configured or os.name != "nt":
        return

    dll_dirs: list[str] = []
    _dll_handles: list = []

    # ── 1. CTranslate2 package directory (primary) ────────────────────
    try:
        import ctranslate2
        ct2_dir = ctranslate2.package_dir
        if os.path.isdir(ct2_dir):
            dll_dirs.append(ct2_dir)
    except ImportError:
        pass

    # ── 2. PyInstaller _internal directory ────────────────────────────
    if getattr(sys, "frozen", False):
        internal = os.path.join(os.path.dirname(sys.executable), "_internal")
        if os.path.isdir(internal):
            dll_dirs.append(internal)
            # CTranslate2 data inside the bundle
            ct2_internal = os.path.join(internal, "ctranslate2")
            if os.path.isdir(ct2_internal):
                dll_dirs.append(ct2_internal)

    # ── 3. nvidia pip package layout (fallback) ───────────────────────
    nvidia_pkgs = (
        "cublas", "cudnn", "cuda_runtime", "cuda_nvrtc",
        "cufft", "curand", "cusolver", "cusparse",
    )
    candidate_roots = list(dict.fromkeys(
        [p for p in sys.path if isinstance(p, str)
         and ("site-packages" in p or "dist-packages" in p)]
        + [
            os.path.join(sys.prefix, "Lib", "site-packages"),
            os.path.join(sys.base_prefix, "Lib", "site-packages"),
        ]
    ))
    for root in candidate_roots:
        for pkg in nvidia_pkgs:
            bin_path = os.path.join(root, "nvidia", pkg, "bin")
            if os.path.isdir(bin_path):
                dll_dirs.append(bin_path)

    # ── De-duplicate and register ─────────────────────────────────────
    dll_dirs = list(dict.fromkeys(dll_dirs))
    if dll_dirs:
        os.environ["PATH"] = (
            os.pathsep.join(dll_dirs)
            + os.pathsep
            + os.environ.get("PATH", "")
        )
        if hasattr(os, "add_dll_directory"):
            for d in dll_dirs:
                try:
                    handle = os.add_dll_directory(d)
                    _dll_handles.append(handle)
                except OSError:
                    pass
        logger.info("CUDA env: registered %d DLL directories.", len(dll_dirs))

    _cuda_env_configured = True


# ── Public API ────────────────────────────────────────────────────────────────

def detect_gpu(force_refresh: bool = False) -> GPUInfo:
    """Detect GPU hardware and CUDA capabilities.

    Results are cached after the first successful detection.  Pass
    ``force_refresh=True`` to re-probe (e.g. after installing CUDA deps).
    """
    global _gpu_info_cache

    if _gpu_info_cache is not None and not force_refresh:
        return _gpu_info_cache

    with _gpu_info_lock:
        if _gpu_info_cache is not None and not force_refresh:
            return _gpu_info_cache

        # Ensure DLL paths are set before probing
        ensure_cuda_env()

        # Try each detection layer in priority order
        info = _detect_via_ctranslate2()
        if info is None:
            info = _detect_via_nvidia_smi()
        if info is None:
            info = _detect_via_dll_probe()
        if info is None:
            info = GPUInfo()  # CPU-only defaults

        _gpu_info_cache = info
        logger.info("GPU detection result: %s", info.summary())
        return info


def optimal_compute_type(gpu_info: Optional[GPUInfo] = None) -> tuple[str, str]:
    """Return (device, compute_type) optimized for the detected hardware.

    Returns
    -------
    tuple[str, str]
        (device, compute_type) ready for ``WhisperModel()``.

        - **CUDA + ≥ 8 GB VRAM** → ``("cuda", "float16")``
        - **CUDA + 4–8 GB VRAM** → ``("cuda", "int8_float16")``
        - **CUDA + < 4 GB VRAM** → ``("cuda", "int8")``
        - **CPU fallback** → ``("cpu", "int8")``
    """
    if gpu_info is None:
        gpu_info = detect_gpu()

    if not gpu_info.cuda_available:
        logger.info("No CUDA — selecting CPU/int8.")
        return ("cpu", "int8")

    vram = gpu_info.vram_total_mb

    # Large VRAM: prioritize quality
    if vram >= 8192:
        if "float16" in gpu_info.compute_types:
            logger.info("GPU %s (%d MB VRAM) → cuda/float16", gpu_info.device_name, vram)
            return ("cuda", "float16")

    # Medium VRAM: balanced quality/memory
    if vram >= 4096:
        if "int8_float16" in gpu_info.compute_types:
            logger.info("GPU %s (%d MB VRAM) → cuda/int8_float16", gpu_info.device_name, vram)
            return ("cuda", "int8_float16")
        if "float16" in gpu_info.compute_types:
            logger.info("GPU %s (%d MB VRAM) → cuda/float16 (no int8_float16 support)", gpu_info.device_name, vram)
            return ("cuda", "float16")

    # Low VRAM or unknown: maximize throughput
    if "int8" in gpu_info.compute_types:
        logger.info("GPU %s (%d MB VRAM) → cuda/int8", gpu_info.device_name, vram)
        return ("cuda", "int8")

    # Fallback: let CTranslate2 decide
    logger.info("GPU %s → cuda/auto (could not determine optimal type)", gpu_info.device_name)
    return ("cuda", "auto")


def optimal_cpu_threads() -> int:
    """Pick a practical CPU thread count for faster-whisper."""
    cores = os.cpu_count() or 4
    if cores <= 4:
        return max(1, cores)
    return min(8, cores - 1)
