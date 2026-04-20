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

import importlib
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

from core.paths import models_dir, data_root, app_bundle_dir

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
# Used as a fallback if the manifest entry does not specify one.
_MIN_MODEL_BIN_BYTES = 1_000_000_000

# Hugging Face Hub timeout/retry tuning for slow or unstable connections.
_DOWNLOAD_RETRIES = 5
_DOWNLOAD_RETRY_BACKOFF_SECONDS = 3.0

# Maximum seconds to wait on a single 429 Retry-After before giving up.
# Prevents the app from hanging for minutes on heavily throttled connections.
_MAX_RATE_LIMIT_WAIT = 90

# Optional Hugging Face token sources (checked in order).
_HF_TOKEN_ENV_KEYS = (
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "HUGGINGFACE_TOKEN",
)


def _read_token_file(path: Path) -> str | None:
    """Read and sanitize a Hugging Face token file.

    Returns ``None`` if the file is missing, unreadable, or empty.
    """
    try:
        if not path.exists() or not path.is_file():
            return None
        token = path.read_text(encoding="utf-8").strip()
        return token or None
    except Exception:
        return None


def _resolve_hf_token() -> str | None:
    """Resolve Hugging Face token from env or local token files.

    Resolution order:
    1. Environment variables (preferred in CI/runtime setups)
    2. ``.hf_token`` under writable data root
    3. ``.hf_token`` next to app bundle/project root

    Never logs token values.
    """
    for key in _HF_TOKEN_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value

    token_files = [
        data_root() / ".hf_token",
        app_bundle_dir() / ".hf_token",
        Path(__file__).resolve().parent.parent / ".hf_token",
    ]
    for token_path in dict.fromkeys(token_files):
        token = _read_token_file(token_path)
        if token:
            return token
    return None


def _load_manifest() -> dict:
    """Load the JSON manifest that declares which models to download."""
    with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def list_available_models() -> list[dict]:
    """Return the list of models declared in the manifest plus any
    user-registered custom models.

    Custom models are local-folder entries added via
    :func:`register_custom_model` and stored in ``setup_state.json``.
    """
    manifest = _load_manifest()
    models = manifest.get("whisper_models")
    if isinstance(models, list) and models:
        base = list(models)
    else:
        single = manifest.get("whisper_model")
        if isinstance(single, dict):
            base = [{
                **single,
                "id": single.get("id", "legacy"),
                "display_name": single.get("display_name", "Whisper"),
            }]
        else:
            base = []

    # Append custom entries persisted in setup state.
    custom = load_setup_state().get("custom_models")
    if isinstance(custom, list):
        base.extend(c for c in custom if isinstance(c, dict) and c.get("id"))
    return base


def get_default_model_id() -> str:
    """Return the manifest-declared default model id."""
    manifest = _load_manifest()
    default = manifest.get("default_model_id")
    if default:
        return str(default)
    models = list_available_models()
    return models[0]["id"] if models else ""


def get_model_entry(model_id: str) -> dict | None:
    """Return the manifest entry for *model_id* or None."""
    for m in list_available_models():
        if m.get("id") == model_id:
            return m
    return None


def _model_min_bytes(entry: dict) -> int:
    return int(entry.get("min_bin_size_bytes", _MIN_MODEL_BIN_BYTES))


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


def model_folder_for_entry(entry: dict) -> Path:
    """Return the on-disk folder containing the model files for *entry*.

    Custom (user-imported) entries carry an absolute ``abs_path``; all
    other entries live under ``models_dir()/<local_dir_name>``.
    """
    abs_path = entry.get("abs_path")
    if abs_path:
        return Path(abs_path)
    return models_dir() / entry["local_dir_name"]


def is_model_ready(model_id: str | None = None) -> bool:
    """Return True only when the target Whisper model is present *and* plausibly complete.

    If *model_id* is None, the currently active model (or the manifest
    default) is used. Readiness requires the expected binary to exist on
    disk and exceed the per-model minimum size threshold.
    """
    if not model_id:
        model_id = get_active_model_id() or get_default_model_id()
    entry = get_model_entry(model_id)
    if not entry:
        return False
    model_path = model_folder_for_entry(entry) / entry.get("expected_file", "model.bin")
    if not model_path.exists():
        return False
    try:
        return model_path.stat().st_size >= _model_min_bytes(entry)
    except OSError:
        return False


def find_ready_model_id() -> str | None:
    """Return the first model id that is currently ready on disk.

    Preference order:
    1) active model id (if ready)
    2) manifest default model id (if ready)
    3) first ready model from the manifest list
    """
    active = get_active_model_id()
    if active and is_model_ready(active):
        return active

    default = get_default_model_id()
    if default and is_model_ready(default):
        return default

    for entry in list_available_models():
        mid = entry.get("id")
        if mid and is_model_ready(mid):
            return str(mid)
    return None


def _legacy_model_roots() -> list[Path]:
    """Return candidate model roots from older/local development layouts."""
    roots: list[Path] = []

    # Dev repo layout (running from source): <project>/models
    roots.append(Path(__file__).resolve().parent.parent / "models")

    # Current working directory layout: <cwd>/models
    roots.append(Path.cwd() / "models")

    # When launched from dist/LocalScribe/LocalScribe.exe inside the repo,
    # the source project root is typically two levels up from the exe dir.
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        roots.append(exe_dir.parent.parent / "models")

    # Keep order, remove duplicates.
    return [Path(p) for p in dict.fromkeys(roots)]


def adopt_legacy_model_if_needed(model_id: str | None = None) -> bool:
    """Try to reuse an already-downloaded model from legacy/local folders.

    Returns ``True`` when the target model is ready after this call (either it
    was already present in ``models_dir()`` or copied there successfully).
    """
    if not model_id:
        model_id = get_active_model_id() or get_default_model_id()
    entry = get_model_entry(model_id)
    if not entry:
        return False

    if is_model_ready(model_id):
        return True

    target_root = models_dir()
    target_dir = target_root / entry["local_dir_name"]
    target_file = target_dir / entry["expected_file"]

    min_bytes = _model_min_bytes(entry)

    for legacy_root in _legacy_model_roots():
        src_dir = legacy_root / entry["local_dir_name"]
        src_file = src_dir / entry["expected_file"]

        if not src_file.exists():
            continue
        try:
            if src_file.stat().st_size < min_bytes:
                continue
        except OSError:
            continue

        try:
            if src_dir.resolve() == target_dir.resolve():
                continue
        except Exception:
            pass

        logging.info("Found existing model in legacy path: %s", src_dir)
        target_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, target_dir, dirs_exist_ok=True)
        logging.info("Copied model to runtime path: %s", target_dir)

        try:
            if target_file.exists() and target_file.stat().st_size >= min_bytes:
                return True
        except OSError:
            pass

    return is_model_ready(model_id)


def get_active_model_id() -> str | None:
    """Return the model id selected by the user during setup, or None."""
    value = load_setup_state().get("active_model_id")
    return str(value) if value else None


def set_active_model_id(model_id: str) -> None:
    """Persist the active model id and refresh the readiness marker."""
    entry = get_model_entry(model_id)
    if not entry:
        raise ValueError(f"Unknown model id: {model_id!r}")
    state = load_setup_state()
    state["active_model_id"] = model_id
    state["model_path"] = str(model_folder_for_entry(entry))
    state["model_ready"] = is_model_ready(model_id)
    save_setup_state(state)


def register_custom_model(folder: str | Path, display_name: str | None = None) -> dict:
    """Register a user-supplied local model folder as a selectable model.

    The *folder* must contain a CTranslate2-format Whisper model: at minimum a
    ``model.bin`` file plus ``config.json``. Tokenizer and vocabulary files are
    optional but recommended.

    Returns the stored entry (with a generated ``id``) on success.
    Raises ``ValueError`` if the folder is not a valid Whisper model.
    """
    folder_path = Path(folder).expanduser().resolve()
    if not folder_path.is_dir():
        raise ValueError(f"Not a folder: {folder_path}")

    model_bin = folder_path / "model.bin"
    config = folder_path / "config.json"
    if not model_bin.exists():
        raise ValueError(
            f"Missing 'model.bin' in {folder_path}. "
            "Select a CTranslate2-format Whisper model folder."
        )
    if not config.exists():
        raise ValueError(
            f"Missing 'config.json' in {folder_path}. "
            "The folder does not look like a Whisper model."
        )
    try:
        bin_size = model_bin.stat().st_size
    except OSError as exc:
        raise ValueError(f"Cannot read model.bin: {exc}") from exc
    try:
        with open(config, "r", encoding="utf-8") as f:
            config_data = json.load(f)
    except Exception as exc:
        raise ValueError(f"Cannot read/parse config.json: {exc}") from exc
    if not isinstance(config_data, dict):
        raise ValueError("config.json is invalid (expected a JSON object).")
    if bin_size < 10_000_000:  # < 10 MB is certainly not a whisper model
        raise ValueError(
            f"'model.bin' is too small ({bin_size} bytes) to be a Whisper model."
        )

    model_id = f"custom:{folder_path.name}"
    # Disambiguate if the user picks two folders with the same leaf name.
    state = load_setup_state()
    custom_models: list[dict] = list(state.get("custom_models") or [])
    existing_ids = {c.get("id") for c in custom_models} | {
        m.get("id") for m in list_available_models() if m.get("id")
    }
    if model_id in existing_ids:
        suffix = 2
        while f"{model_id}#{suffix}" in existing_ids:
            suffix += 1
        model_id = f"{model_id}#{suffix}"

    entry = {
        "id": model_id,
        "display_name": display_name or f"Local · {folder_path.name}",
        "tier": "Local folder",
        "description": f"Imported from {folder_path}",
        "approx_size_mb": max(1, bin_size // (1024 * 1024)),
        "min_bin_size_bytes": max(1, bin_size),
        "local_dir_name": folder_path.name,
        "expected_file": "model.bin",
        "abs_path": str(folder_path),
        "custom": True,
        "repo_id": "",
    }

    # Replace any stale entry with the same abs_path (re-imported folder).
    custom_models = [c for c in custom_models if c.get("abs_path") != entry["abs_path"]]
    custom_models.append(entry)
    state["custom_models"] = custom_models
    save_setup_state(state)
    return entry


# ── Hugging Face token storage ────────────────────────────────────────

def hf_token_path() -> Path:
    """Return the on-disk location of the saved Hugging Face token."""
    return data_root() / ".hf_token"


def save_hf_token(token: str) -> None:
    """Persist *token* securely under data_root().

    Performs an atomic write and best-effort 0600 permissions on POSIX.
    Never logs the token value.
    """
    token = (token or "").strip()
    if not token:
        raise ValueError("Refusing to save empty Hugging Face token.")
    path = hf_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(token)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_hf_token() -> None:
    """Remove any saved Hugging Face token. Safe if missing."""
    try:
        hf_token_path().unlink(missing_ok=True)
    except OSError:
        pass


def validate_hf_token(token: str) -> tuple[bool, str]:
    """Validate *token* by hitting the Hugging Face whoami endpoint.

    Returns (True, username) on success, or (False, error_message) on
    failure. The token value itself is never logged.
    """
    token = (token or "").strip()
    if not token:
        return False, "Token is empty."
    try:
        api = HfApi(token=token)
        info = api.whoami()
        name = (
            (isinstance(info, dict) and (info.get("name") or info.get("fullname")))
            or "authenticated user"
        )
        return True, str(name)
    except Exception as exc:
        return False, str(exc)


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


def _format_eta(seconds: float) -> str:
    """Format a remaining-time estimate into a short human-readable string."""
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


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
    speed_update = Signal(str, str)  # (speed_text, eta_text)
    finished = Signal(bool, str)

    def __init__(self, model_id: str | None = None, hf_token: str | None = None):
        super().__init__()
        self._cancel_requested = False
        self._global_bytes_total = 0
        self._global_bytes_done = 0
        self._current_file_done = 0
        self.model_id = model_id or get_default_model_id()
        self._explicit_token = (hf_token or "").strip() or None
        # Speed/ETA tracking state
        self._speed_last_time: float | None = None
        self._speed_last_bytes: int = 0
        self._speed_ema: float = 0.0
        self._last_speed_emit: float = 0.0

    def chunk_downloaded(self, n_bytes: int):
        self._current_file_done = n_bytes
        current_total = self._global_bytes_done + n_bytes
        if self._global_bytes_total > 0:
            pct = int((current_total / self._global_bytes_total) * 100)
            self.progress.emit(min(99, pct))
        self._update_speed(current_total)

    def _update_speed(self, current_total: int) -> None:
        """Compute EMA-smoothed download speed and ETA, emit every ~400ms."""
        now = time.monotonic()
        if self._speed_last_time is None:
            self._speed_last_time = now
            self._speed_last_bytes = current_total
            return
        dt = now - self._speed_last_time
        if dt < 0.5:
            return
        db = max(0, current_total - self._speed_last_bytes)
        instant = (db / dt) if dt > 0 else 0.0
        if self._speed_ema <= 0:
            self._speed_ema = instant
        else:
            self._speed_ema = 0.3 * instant + 0.7 * self._speed_ema
        self._speed_last_time = now
        self._speed_last_bytes = current_total
        if now - self._last_speed_emit < 0.4:
            return
        remaining = max(0, self._global_bytes_total - current_total)
        eta_seconds = int(remaining / self._speed_ema) if self._speed_ema > 1 else -1
        self.speed_update.emit(_format_speed(self._speed_ema), _format_eta(eta_seconds))
        self._last_speed_emit = now

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
        token: str | None,
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
                    token=token,
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

    def _download_repo_with_progress(self, repo_id: str, local_dir: Path, token: str | None) -> None:
        """Download every file in a Hugging Face model repo with live progress.

        Progress tracking
        -----------------
        We monkey-patch ``huggingface_hub.file_download.tqdm`` with a custom
        subclass (``_ProgressTqdm``) that intercepts every ``update(n)`` call.
        Each call carries the cumulative byte count for the current file.
        Combined with the known per-file sizes from the repo manifest, this
        gives a smooth, accurate overall percentage that increments naturally
        as bytes arrive over the network.

        hf_xet and hf_transfer are explicitly disabled so that downloads
        always flow through the standard HTTP tqdm-instrumented path.
        """
        api = HfApi(token=token)
        info = api.model_info(repo_id)
        revision = info.sha or "main"  # pin to exact commit for reproducibility

        # Relax HF Hub default timeouts for multi-GB model downloads.
        # These env vars are respected by huggingface_hub.
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
        os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")

        # Enable hf_xet for the Xet CDN when available. If hf_xet is missing
        # or fails to load (e.g. DLL/runtime issue), gracefully fall back to
        # standard HTTP downloads instead of failing setup.
        try:
            importlib.import_module("hf_xet")
            os.environ["HF_HUB_ENABLE_HF_XET"] = "1"
            self._log("hf_xet detected: accelerated HF download path enabled.")
        except Exception as exc:
            os.environ["HF_HUB_ENABLE_HF_XET"] = "0"
            self._log(
                "hf_xet is unavailable in this runtime; falling back to standard downloads. "
                f"Details: {exc}"
            )

        # hf_transfer stays OFF: it is faster but bypasses tqdm so we would
        # lose the live %, MB/s and ETA in the setup wizard.
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

                self._download_file_with_retry(
                    repo_id=repo_id,
                    filename=filename,
                    revision=revision,
                    local_dir=local_dir,
                    token=token,
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
        """Orchestrate the full first-run setup for ``self.model_id``.

        Steps:
        1. Create data directories (``data_root()``, ``models_dir()``).
        2. If the selected model is already downloaded and valid, skip.
        3. Otherwise download every file from the Hugging Face repo.
        4. Validate that ``model.bin`` landed correctly.
        5. Persist the active model id so the rest of the app can load it.
        """
        # ── 1. Ensure data directories ────────────────────────────────
        self.status_update.emit("Preparing data directories...")
        self.progress.emit(-1)
        data_root().mkdir(parents=True, exist_ok=True)
        models_dir()  # creates if needed

        # ── 2. Resolve selected model and auth token ──────────────────
        entry = get_model_entry(self.model_id)
        if not entry:
            raise RuntimeError(f"Unknown model id: {self.model_id!r}")
        hf_token = self._explicit_token or _resolve_hf_token()
        local_dir = models_dir() / entry["local_dir_name"]
        expected_file = local_dir / entry["expected_file"]
        self._log(f"Selected model: {entry['display_name']} ({entry['id']})")
        self._log(f"Install location: {local_dir}")

        if is_model_ready(self.model_id):
            self.status_update.emit(f"{entry['display_name']} already downloaded.")
            self._log(f"Model already present: {expected_file}")
            self.progress.emit(100)
        else:
            self.status_update.emit(
                f"Downloading {entry['display_name']} (~{entry.get('approx_size_mb', '?')} MB)..."
            )
            self.progress.emit(-1)

            self._log(f"Download target directory: {local_dir}")
            self._log(
                "If download is slow: common reasons are internet speed, "
                "Hugging Face server load, antivirus scanning, and disk write speed."
            )
            try:
                self._download_repo_with_progress(entry["repo_id"], local_dir, hf_token)
            except Exception:
                if self._cancel_requested:
                    self._cleanup_partial_download(local_dir)
                raise

            if not is_model_ready(self.model_id):
                raise RuntimeError(
                    "Download completed but model appears incomplete or missing. "
                    f"Expected file: {expected_file}"
                )

            self.status_update.emit("Model downloaded successfully.")
            self.progress.emit(100)

        # ── 3. Persist active model so the rest of the app uses it ────
        set_active_model_id(self.model_id)

        self.status_update.emit("Setup complete!")
