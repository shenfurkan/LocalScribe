"""core/cuda_installer.py — CUDA library management.

Downloads and manages NVIDIA CUDA libraries (cuBLAS, cuDNN) required
for GPU-accelerated transcription.  Libraries are stored in the user
data directory (``cuda_libs_dir()``) so they persist across app updates.

Strategy
--------
1. Query PyPI JSON API for the latest ``nvidia-cublas-cu12`` and
   ``nvidia-cudnn-cu12`` wheel URLs.
2. Download the ``.whl`` files (which are standard ZIP archives).
3. Extract only the DLL files into ``cuda_libs_dir()``.
4. ``gpu_manager.ensure_cuda_env()`` already scans this directory,
   so the libraries are immediately available to CTranslate2.
"""

import io
import json
import logging
import os
import shutil
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request

from PySide6.QtCore import QObject, Signal

from core.paths import cuda_libs_dir

logger = logging.getLogger(__name__)

# ── Package definitions ──────────────────────────────────────────────────────

CUDA_PACKAGES = [
    {
        "pypi_name": "nvidia-cublas-cu12",
        "dll_prefix": "nvidia/cublas/bin/",
        "check_dll": "cublas64_12.dll",
        "label": "cuBLAS (matrix computation)",
    },
    {
        "pypi_name": "nvidia-cudnn-cu12",
        "dll_prefix": "nvidia/cudnn/bin/",
        "check_dll": "cudnn64_9.dll",
        "label": "cuDNN (deep learning)",
    },
]


# ── Status helpers ───────────────────────────────────────────────────────────

def cuda_lib_status() -> dict:
    """Return a snapshot of which CUDA libraries are present.

    Returns
    -------
    dict
        ``libs_dir``       – str, directory being scanned
        ``packages``       – list of per-package dicts with keys
                             *label*, *check_dll*, *installed*, *path*
        ``all_installed``  – bool, True if every required DLL is present
    """
    libs_dir = cuda_libs_dir()
    packages = []
    for pkg in CUDA_PACKAGES:
        dll_path = libs_dir / pkg["check_dll"]
        packages.append({
            "label": pkg["label"],
            "check_dll": pkg["check_dll"],
            "installed": dll_path.exists(),
            "path": str(dll_path) if dll_path.exists() else None,
        })
    return {
        "libs_dir": str(libs_dir),
        "packages": packages,
        "all_installed": all(p["installed"] for p in packages),
    }


# ── Download helpers ─────────────────────────────────────────────────────────

def _find_wheel_url(pypi_name: str) -> tuple[str, int]:
    """Query PyPI for the latest win_amd64 wheel URL and its size in bytes."""
    url = f"https://pypi.org/pypi/{pypi_name}/json"
    req = Request(url, headers={"Accept": "application/json",
                                "User-Agent": "LocalScribe/1.0"})
    with urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())

    version = data["info"]["version"]
    files = data["releases"].get(version, [])

    for f in files:
        if f["filename"].endswith("-win_amd64.whl"):
            return f["url"], f.get("size", 0)

    raise RuntimeError(
        f"No Windows (win_amd64) wheel found for {pypi_name} {version}. "
        "GPU acceleration requires a Windows x64 system."
    )


def _download_and_extract_dlls(
    pypi_name: str,
    dll_prefix: str,
    target_dir: Path,
    progress_cb=None,
    cancel_check=None,
) -> list[str]:
    """Download a wheel from PyPI and extract its DLLs into *target_dir*.

    Parameters
    ----------
    progress_cb : callable(downloaded: int, total: int, name: str), optional
    cancel_check : callable() -> bool, optional
        Return True to abort the download.

    Returns
    -------
    list[str]
        Basenames of extracted DLL files.
    """
    wheel_url, total_size = _find_wheel_url(pypi_name)
    logger.info("Downloading %s (%d MB) from %s",
                pypi_name, total_size // (1024 * 1024), wheel_url)

    req = Request(wheel_url, headers={"User-Agent": "LocalScribe/1.0"})
    chunks: list[bytes] = []
    downloaded = 0
    chunk_size = 256 * 1024  # 256 KB

    with urlopen(req, timeout=30) as resp:
        # Use Content-Length if PyPI didn't supply size in metadata.
        if not total_size:
            total_size = int(resp.headers.get("Content-Length", 0))
        while True:
            if cancel_check and cancel_check():
                return []
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            chunks.append(chunk)
            downloaded += len(chunk)
            if progress_cb:
                progress_cb(downloaded, total_size, pypi_name)

    wheel_data = b"".join(chunks)
    extracted: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(wheel_data)) as zf:
        for entry in zf.namelist():
            if entry.startswith(dll_prefix) and entry.lower().endswith(".dll"):
                dll_name = os.path.basename(entry)
                target_path = target_dir / dll_name
                with zf.open(entry) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted.append(dll_name)
                logger.info("Extracted: %s → %s", dll_name, target_path)

    return extracted


# ── Qt worker ────────────────────────────────────────────────────────────────

class CudaInstallWorker(QObject):
    """Background worker that downloads and installs CUDA libraries."""

    progress = Signal(int, int, str)   # (downloaded_bytes, total_bytes, package)
    status   = Signal(str)             # human-readable status line
    finished = Signal(list)            # list of extracted DLL names
    error    = Signal(str)

    def __init__(self):
        super().__init__()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            target = cuda_libs_dir()
            all_extracted: list[str] = []

            for pkg in CUDA_PACKAGES:
                if self._cancelled:
                    return

                if (target / pkg["check_dll"]).exists():
                    self.status.emit(f"✓ {pkg['label']} already installed")
                    continue

                self.status.emit(f"Downloading {pkg['label']}…")
                extracted = _download_and_extract_dlls(
                    pypi_name=pkg["pypi_name"],
                    dll_prefix=pkg["dll_prefix"],
                    target_dir=target,
                    progress_cb=lambda d, t, n: self.progress.emit(d, t, n),
                    cancel_check=lambda: self._cancelled,
                )
                if self._cancelled:
                    return
                all_extracted.extend(extracted)
                self.status.emit(f"✓ {pkg['label']} installed ({len(extracted)} files)")

            self.finished.emit(all_extracted)
        except Exception as exc:
            logger.error("CUDA install failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Uninstall ────────────────────────────────────────────────────────────────

def uninstall_cuda_libs() -> None:
    """Remove all downloaded CUDA DLLs from the user data directory."""
    libs_dir = cuda_libs_dir()
    if libs_dir.exists():
        shutil.rmtree(libs_dir, ignore_errors=True)
    logger.info("CUDA libraries removed from %s", libs_dir)
