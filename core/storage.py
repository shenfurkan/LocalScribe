"""
core/storage.py

Manages transcript persistence as JSON files under a ``transcripts/``
directory, with a fast index for listing/sorting without reading every file.
"""
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from core.paths import transcripts_dir

# A strict UUID-v4 pattern used to validate transcript IDs before we build
# file paths from them.  This prevents any path-traversal attack.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _valid_id(transcript_id: str) -> bool:
    """Returns True iff *transcript_id* is a well-formed UUID v4 string."""
    return bool(_UUID_RE.match(transcript_id))


class StorageManager:
    def __init__(self):
        self.transcripts_dir = transcripts_dir()
        self.index_file = self.transcripts_dir / "index.json"
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)
        self._index_cache: dict = {}
        self.settings: dict = {"theme": "dark"}
        self._load_index()

    # ── Index I/O ─────────────────────────────────────────────────────────────

    def _load_index(self) -> None:
        if self.index_file.exists():
            try:
                with open(self.index_file, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                # Pop settings first so _index_cache stays clean.
                if isinstance(raw, dict):
                    self.settings = raw.pop("_settings", self.settings)
                    self._index_cache = raw
                return
            except Exception as exc:
                logging.warning("Could not load transcript index: %s. Rebuilding…", exc)

        # Fallback: rebuild from individual JSON files.
        self._index_cache = {}
        for fp in self.transcripts_dir.glob("*.json"):
            if fp.name == "index.json":
                continue
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                tid = data.get("id")
                if tid and _valid_id(tid):
                    self._index_cache[tid] = self._extract_meta(data)
            except Exception:
                pass  # silently skip corrupted files
        self._save_index()

    def _save_index(self) -> None:
        try:
            payload = {**self._index_cache, "_settings": self.settings}
            tmp_path = self.index_file.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            tmp_path.replace(self.index_file)
        except Exception as exc:
            logging.error("Error saving index: %s", exc)

    # ── Settings ──────────────────────────────────────────────────────────────

    def get_setting(self, key: str, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key: str, value) -> None:
        self.settings[key] = value
        self._save_index()

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_meta(transcript: dict) -> dict:
        return {
            "id":               transcript.get("id"),
            "name":             transcript.get("name"),
            "status":           transcript.get("status"),
            "duration_seconds": transcript.get("duration_seconds"),
            "created_at":       transcript.get("created_at"),
            "language":         transcript.get("language"),
        }

    def _path_for(self, transcript_id: str) -> Path:
        """Returns the expected file path for *transcript_id*.

        Raises ``ValueError`` if the ID is not a valid UUID to prevent
        path-traversal attacks.
        """
        if not _valid_id(transcript_id):
            raise ValueError(f"Invalid transcript ID: {transcript_id!r}")
        return self.transcripts_dir / f"{transcript_id}.json"

    # ── Public CRUD ───────────────────────────────────────────────────────────

    def save(self, transcript: dict) -> str:
        """Persist *transcript* to disk and update the index. Returns the ID."""
        if "id" not in transcript:
            transcript["id"] = str(uuid.uuid4())
        if "created_at" not in transcript:
            transcript["created_at"] = datetime.now().isoformat()

        fp = self._path_for(transcript["id"])
        tmp_path = fp.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(transcript, fh, indent=2, ensure_ascii=False)
        tmp_path.replace(fp)

        self._index_cache[transcript["id"]] = self._extract_meta(transcript)
        self._save_index()
        return transcript["id"]

    def load(self, transcript_id: str) -> dict | None:
        """Load and return a single transcript by ID, or *None* if missing."""
        try:
            fp = self._path_for(transcript_id)
        except ValueError:
            logging.warning("Attempted to load transcript with invalid ID: %r", transcript_id)
            return None
        if fp.exists():
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as exc:
                logging.error("Could not read transcript %s: %s", transcript_id, exc)
        return None

    def load_all(self) -> list[dict]:
        """Return metadata for all transcripts, sorted newest-first."""
        transcripts = list(self._index_cache.values())
        transcripts.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return transcripts

    def delete(self, transcript_id: str) -> None:
        """Permanently delete the transcript file and remove it from the index."""
        try:
            fp = self._path_for(transcript_id)
        except ValueError:
            logging.warning("Attempted to delete transcript with invalid ID: %r", transcript_id)
            return
        try:
            fp.unlink(missing_ok=True)
        except Exception as exc:
            logging.error("Could not delete transcript file %s: %s", fp, exc)

        if transcript_id in self._index_cache:
            del self._index_cache[transcript_id]
            self._save_index()

    def rename(self, transcript_id: str, new_name: str) -> None:
        """Update the ``name`` field of an existing transcript."""
        data = self.load(transcript_id)
        if data:
            data["name"] = new_name
            self.save(data)


