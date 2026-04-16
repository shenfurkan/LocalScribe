"""
core/setup_manager.py — First-run health checks and model download.

Responsibilities
----------------
* Determine whether the Whisper model is already present on disk
  (``is_model_ready()``).
* Download the model from Hugging Face Hub with per-file progress,
  speed reporting, and cancellation support (``SetupWorker``).
* Persist a ``setup_state.json`` marker so we can track which models
  have been downloaded without re-scanning every file.

Design notes
------------
``SetupWorker`` is a ``QObject`` that runs on a ``QThread`` via
``moveToThread()``.  It communicates with the UI exclusively through
Qt signals (``status_update``, ``progress``, ``finished``).  This is
the recommended Qt threading pattern — no direct attribute access
across threads, no shared mutable state.

The manifest (``runtime_manifest.json``) declares which Hugging Face
repo to download, the expected subdirectory name, and the filename
that must exist for the model to be considered “ready”.
"""

import json
import logging
import os
import re
import shutil
import socket
import sys
import time
from pathlib import Path

import threading

from PySide6.QtCore import QObject, Signal
from huggingface_hub import HfApi, hf_hub_download
import huggingface_hub.utils as hf_utils

from core.paths import models_dir, data_root

_worker_ctx = threading.local()

class _ProgressTqdm(hf_utils.tqdm):
    def update(self, n=1):
        super().update(n)
        worker = getattr(_worker_ctx, "current_worker", None)
        if worker and self.total:
            worker.chunk_downloaded(self.n)

# Path to the JSON manifest that declares which models to download.
# In a PyInstaller bundle this resolves to  _internal/core/runtime_manifest.json
# because build.py passes ``--add-data core;core``.
_MANIFEST_PATH = Path(__file__).parent / "runtime_manifest.json"

# Name of the per-user marker file that records completed setup steps.
_SETUP_STATE_FILE = "setup_state.json"

# Minimum file size (bytes) for model.bin to be considered complete.
# The real file is ~3 GB; 1 GB catches partial / interrupted downloads.
_MIN_MODEL_BIN_BYTES = 1_000_000_000

# Hugging Face Hub timeout/retry tuning for slow or unstable connections.
_DOWNLOAD_RETRIES = 5
_DOWNLOAD_RETRY_BACKOFF_SECONDS = 3.0

# Maximum seconds to wait on a single 429 Retry-After before giving up.
# Prevents the app from hanging for minutes on heavily throttled connections.
_MAX_RATE_LIMIT_WAIT = 90


def _load_manifest() -> dict:
    """Load the JSON manifest that declares which models to download."""
    with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _setup_state_path() -> Path:
    """Return the path to the setup state file."""
    return data_root() / _SETUP_STATE_FILE


def load_setup_state() -> dict:
    """Load the setup state from disk, or return an empty dict if not found."""
    p = _setup_state_path()
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_setup_state(state: dict) -> None:
    """Atomically write the setup state to disk.

    Writes to a ``.tmp`` file first and then renames it into place.
    ``Path.replace()`` is atomic on the same filesystem (POSIX
    guarantee; best-effort on Windows NTFS).
    """
    p = _setup_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    try:
        import os
        os.replace(tmp, p)
    except Exception:
        import shutil
        shutil.move(tmp, p)


def is_model_ready() -> bool:
    """Return True only when the Whisper model binary is present *and* plausibly complete.

    Checks performed:
    1. The expected file (``model.bin``) exists on disk under
       ``models_dir() / <local_dir_name>``.
    2. Its size is at least ``_MIN_MODEL_BIN_BYTES`` (1 GB) to reject
       partial downloads or empty placeholder files.

    This function is called from three places:
    * ``main.py`` — first-run gate (decides whether to show SetupDialog).
    * ``dashboard_page.py`` — pre-flight check before starting transcription.
    * ``SetupWorker._do_setup()`` — skip download if model already exists.
    """
    manifest = _load_manifest()
    info = manifest["whisper_model"]
    model_path = models_dir() / info["local_dir_name"] / info["expected_file"]
    if not model_path.exists():
        return False
    try:
        return model_path.stat().st_size >= _MIN_MODEL_BIN_BYTES
    except OSError:
        return False


def _format_bytes(num_bytes: float) -> str:
    """Format a byte count into a human-readable string."""
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(0.0, num_bytes))
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def _format_speed(bytes_per_sec: float) -> str:
    """Format a byte-per-second speed into a human-readable string."""
    return f"{_format_bytes(bytes_per_sec)}/s"


class SetupWorker(QObject):
    """
    Performs first-run setup in a background thread.

    Signals
    -------
    status_update(str)   — human-readable status text
    progress(int)        — 0-100 percentage (or -1 for indeterminate)
    finished(bool, str)  — (success, error_message_or_empty)
    """

    status_update = Signal(str)
    log_update = Signal(str)
    progress = Signal(int)
    file_status = Signal(str)
    finished = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self._cancel_requested = False
        self._global_bytes_total = 0
        self._global_bytes_done = 0
        self._current_file_done = 0

    def chunk_downloaded(self, n_bytes: int):
        self._current_file_done = n_bytes
        current_total = self._global_bytes_done + n_bytes
        if self._global_bytes_total > 0:
            pct = int((current_total / self._global_bytes_total) * 100)
            self.progress.emit(min(99, pct))

    def _log(self, text: str) -> None:
        """Log a message to the console and emit a signal."""
        self.log_update.emit(text)
        logging.info(text)

    def request_cancel(self) -> None:
        """Request cancellation of the setup process."""
        self._cancel_requested = True
        self._log("Cancellation requested. Cleaning up partial files...")

    def _check_cancel(self) -> None:
        """Check if cancellation has been requested."""
        if self._cancel_requested:
            raise RuntimeError("Setup cancelled by user.")

    def _cleanup_partial_download(self, local_dir: Path) -> None:
        """Clean up a partial download."""
        if local_dir.exists():
            shutil.rmtree(local_dir, ignore_errors=True)
            self._log(f"Removed partial download folder: {local_dir}")

    # ── Error classification helpers ──────────────────────────────────────

    def _is_timeout_error(self, exc: Exception) -> bool:
        """Best-effort timeout detection across requests/httpx/socket stacks."""
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return True
        text = str(exc).lower()
        return "timed out" in text or "timeout" in text

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        """Detect HTTP 429 Too Many Requests from huggingface_hub exceptions."""
        text = str(exc)
        # huggingface_hub raises HTTPError / RepositoryNotFoundError with
        # the status code embedded in the message string.
        return "429" in text or "too many requests" in text.lower()

    def _parse_retry_after(self, exc: Exception) -> float:
        """Extract Retry-After seconds from a 429 exception message.

        huggingface_hub embeds the server response body in the exception
        string.  We look for any numeric value following "retry-after",
        "retry after", or "wait" keywords.  Falls back to
        ``_DOWNLOAD_RETRY_BACKOFF_SECONDS`` if nothing is found.

        Security note: we only read the number — no URLs, headers, or
        other server-provided content are used or stored.
        """
        text = str(exc).lower()
        patterns = [
            r"retry[- ]after[:\s]+(\d+)",
            r"wait[\s:]+(\d+)\s*s",
            r"\bafter\s+(\d+)\s*second",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return min(float(m.group(1)), _MAX_RATE_LIMIT_WAIT)
        return _DOWNLOAD_RETRY_BACKOFF_SECONDS

    def _retryable(self, exc: Exception) -> bool:
        """True if the error is transient and worth retrying."""
        return self._is_timeout_error(exc) or self._is_rate_limit_error(exc)

    # ── Download with smart retry ──────────────────────────────────────────

    def _download_file_with_retry(
        self,
        *,
        repo_id: str,
        filename: str,
        revision: str,
        local_dir: Path,
    ) -> str:
        """Download a single repo file with 429-aware and timeout-aware retries.

        Retry strategy
        --------------
        * **Rate-limited (429)** — reads the ``Retry-After`` value from the
          exception message and waits exactly that long (capped at
          ``_MAX_RATE_LIMIT_WAIT`` seconds) before retrying.  The UI shows
          a countdown message so the user knows the app is not frozen.
        * **Timeout** — exponential backoff (``_DOWNLOAD_RETRY_BACKOFF_SECONDS
          × attempt``).
        * **Other errors** — re-raised immediately; no retry.

        Security
        --------
        No credentials, tokens, or user-identifiable data are read, stored,
        or transmitted.  Only the numeric wait value from the server response
        is consumed.
        """
        last_exc: Exception | None = None
        for attempt in range(1, _DOWNLOAD_RETRIES + 1):
            self._check_cancel()
            try:
                return hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    revision=revision,
                    local_dir=str(local_dir),
                )
            except Exception as exc:
                last_exc = exc

                if not self._retryable(exc) or attempt >= _DOWNLOAD_RETRIES:
                    raise

                if self._is_rate_limit_error(exc):
                    wait_for = self._parse_retry_after(exc)
                    self._log(
                        f"Hugging Face rate limit reached. "
                        f"Waiting {wait_for:.0f}s before retrying "
                        f"({attempt}/{_DOWNLOAD_RETRIES})..."
                    )
                    # Countdown in the UI so users don't think it's frozen
                    for remaining in range(int(wait_for), 0, -1):
                        self._check_cancel()
                        self.status_update.emit(
                            f"Rate limited — resuming in {remaining}s "
                            f"({attempt}/{_DOWNLOAD_RETRIES} retries used)"
                        )
                        time.sleep(1)
                else:
                    wait_for = _DOWNLOAD_RETRY_BACKOFF_SECONDS * attempt
                    self._log(
                        f"Network timeout on '{filename}'. "
                        f"Retrying ({attempt}/{_DOWNLOAD_RETRIES}) in {wait_for:.0f}s..."
                    )
                    self.status_update.emit(
                        f"Slow connection — retrying in {wait_for:.0f}s "
                        f"({attempt}/{_DOWNLOAD_RETRIES})"
                    )
                    time.sleep(wait_for)

        raise RuntimeError(str(last_exc) if last_exc else "Download failed after retries.")

    def _download_repo_with_progress(self, repo_id: str, local_dir: Path) -> None:
        """Download every file in a Hugging Face model repo with live progress.

        Progress tracking
        -----------------
        ``hf_xet`` (the accelerated backend) bypasses tqdm entirely — it
        downloads chunks into its own content-addressed cache and only hands
        the assembled file to the HF layer when the transfer is complete.
        A ``tqdm_class`` hook therefore receives at most one large
        ``update(n)`` call per file, making the bar appear frozen then jump.

        Instead, a lightweight daemon thread polls the directories where the
        active download backend writes its data — ``local_dir``, the HF hub
        model cache, and the xet chunk cache — every 0.75 s.  The aggregate
        byte count minus the pre-download baseline gives a smooth, real-time
        progress value that works regardless of which backend is in use.

        Parameters
        ----------
        repo_id : str
            Hugging Face repo identifier, e.g. ``Systran/faster-whisper-large-v3``.
        local_dir : Path
            Target directory.  ``hf_hub_download(local_dir=...)`` preserves the
            repo's file structure under this path.
        """
        api = HfApi()
        info = api.model_info(repo_id)
        revision = info.sha or "main"  # pin to exact commit for reproducibility

        # Relax HF Hub default timeouts for multi-GB model downloads.
        # These env vars are respected by huggingface_hub.
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
        os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")

        # Force disabled HF transfer methods to ensure pure chunk streaming with tqdm
        os.environ["HF_HUB_ENABLE_HF_XET"] = "0"
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
        
        # Monkey patch Hugging Face's tqdm instance in file_download where it is actually used
        import huggingface_hub.file_download as hf_fd
        original_tqdm = hf_fd.tqdm
        hf_fd.tqdm = _ProgressTqdm
        _worker_ctx.current_worker = self

        siblings = [
            s for s in (info.siblings or [])
            if getattr(s, "rfilename", None) and s.rfilename != ".gitattributes"
        ]
        if not siblings:
            raise RuntimeError(f"No files found in model repository: {repo_id}")

        self._global_bytes_total = sum((getattr(s, "size", 0) or 0) for s in siblings)
        self._global_bytes_done = 0
        total_files = len(siblings)

        self.progress.emit(0)
        self.status_update.emit("Downloading Model Data")

        try:
            for idx, sibling in enumerate(siblings, start=1):
                self._check_cancel()
                self._current_file_done = 0
                filename = sibling.rfilename
                self.file_status.emit(f"File {idx} of {total_files}: {filename}")

                local_path = self._download_file_with_retry(
                    repo_id=repo_id,
                    filename=filename,
                    revision=revision,
                    local_dir=local_dir,
                )

                file_size = getattr(sibling, "size", 0) or 0
                self._global_bytes_done += file_size
        finally:
            # Restore class attributes context properly
            hf_fd.tqdm = original_tqdm
            _worker_ctx.current_worker = None



    def run(self):
        try:
            self._do_setup()
            self.finished.emit(True, "")
        except Exception as exc:
            logging.error("Setup failed: %s", exc, exc_info=True)
            self.finished.emit(False, str(exc))

    def _do_setup(self):
        """Orchestrate the full first-run setup.

        Steps:
        1. Create data directories (``data_root()``, ``models_dir()``).
        2. If the model is already downloaded and valid, skip.
        3. Otherwise download every file from the Hugging Face repo.
        4. Validate that ``model.bin`` landed correctly and is >= 1 GB.
        5. Persist a ``setup_state.json`` marker for future reference.
        """
        # ── 1. Ensure data directories ────────────────────────────────
        self.status_update.emit("Preparing data directories...")
        self.progress.emit(-1)
        data_root().mkdir(parents=True, exist_ok=True)
        models_dir()  # creates if needed

        # ── 2. Download whisper model if missing ──────────────────────
        manifest = _load_manifest()
        info = manifest["whisper_model"]
        local_dir = models_dir() / info["local_dir_name"]
        expected_file = local_dir / info["expected_file"]
        self._log(f"Install location: {local_dir}")

        # is_model_ready() does a robust check: file exists AND >= 1 GB.
        if is_model_ready():
            self.status_update.emit("Whisper model already downloaded.")
            self._log(f"Model already present: {expected_file}")
            self.progress.emit(100)
        else:
            self.status_update.emit(
                f"Downloading {info['description']}...\n"
                "This only happens once. Please keep the app open."
            )
            self.progress.emit(-1)

            self._log(f"Download target directory: {local_dir}")
            self._log(
                "If download is slow: common reasons are internet speed, "
                "Hugging Face server load, antivirus scanning, and disk write speed."
            )
            try:
                self._download_repo_with_progress(info["repo_id"], local_dir)
            except Exception:
                if self._cancel_requested:
                    self._cleanup_partial_download(local_dir)
                raise

            # Post-download validation — make sure the binary actually
            # landed and exceeds our minimum size threshold.
            if not is_model_ready():
                raise RuntimeError(
                    "Download completed but model appears incomplete or missing. "
                    f"Expected file: {expected_file}"
                )

            self.status_update.emit("Model downloaded successfully.")
            self.progress.emit(100)

        # ── 3. Persist setup completion marker ───────────────────────
        # This JSON file is informational — the real readiness check
        # always goes through is_model_ready() which inspects the
        # actual binary on disk.
        state = load_setup_state()
        state["model_ready"] = True
        state["model_path"] = str(local_dir)
        save_setup_state(state)

        self.status_update.emit("Setup complete!")
