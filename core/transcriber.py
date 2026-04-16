"""core/transcriber.py — Background transcription workers.

Contains two QObject workers designed for the Qt moveToThread() pattern:

* ``TranscriptionWorker`` — runs a full transcription job (model load,
  segment iteration, optional tail recovery) and streams results to the
  UI via signals.
* ``ModelPreloadWorker`` — warms the model singleton at app startup so
  that the first transcription starts instantly.

Transcription profiles
----------------------
Each profile is a pre-tuned set of VAD and decoding parameters:

* **balanced** — Default.  Good for typical speech with moderate pauses.
* **pause_resilient** — Shorter silence threshold + lower no_speech
  threshold to avoid dropping speech around long pauses or music.
* **no_vad** — Disables Silero VAD entirely.  Useful when VAD
  aggressively clips speech.

Tail recovery
-------------
After the main pass finishes, if a significant gap remains between the
last decoded segment and the audio duration, a second pass runs over
the tail with VAD disabled (``no_vad`` profile).  This recovers speech
that Silero VAD incorrectly classified as silence (e.g. music, ambient
noise, or quiet speakers).
"""

import logging
from PySide6.QtCore import QObject, Signal
from core.model_manager import get_model


# ---------------------------------------------------------------------------
# Transcription profiles — pre-tuned parameter sets for different
# audio characteristics.  Users select a profile in the language dialog
# before starting a transcription.
# ---------------------------------------------------------------------------
TRANSCRIPTION_PROFILES: dict[str, dict] = {
    "balanced": {
        "vad_filter": True,
        "vad_parameters": {
            "min_silence_duration_ms": 1500,
            "speech_pad_ms": 300,
            "max_speech_duration_s": 30,
        },
        "no_speech_threshold": 0.55,
        "log_prob_threshold": -1.2,
        "compression_ratio_threshold": 2.6,
        "hallucination_silence_threshold": 2.0,
    },
    "pause_resilient": {
        "vad_filter": True,
        "vad_parameters": {
            "min_silence_duration_ms": 900,
            "speech_pad_ms": 220,
            "max_speech_duration_s": 20,
        },
        "no_speech_threshold": 0.4,
        "log_prob_threshold": -1.8,
        "compression_ratio_threshold": 2.9,
        "hallucination_silence_threshold": 1.2,
    },
    "no_vad": {
        "vad_filter": False,
        "vad_parameters": None,
        "no_speech_threshold": 0.4,
        "log_prob_threshold": -1.8,
        "compression_ratio_threshold": 2.9,
        "hallucination_silence_threshold": 1.2,
    },
}

# ---------------------------------------------------------------------------
# Tail recovery thresholds — trigger a second pass over the end of the
# audio if the gap between the last decoded segment and the total
# duration is significant.  This catches speech that VAD missed.
# ---------------------------------------------------------------------------
TAIL_RECOVERY_MIN_GAP_SECONDS = 20.0   # absolute: at least 20 s uncovered
TAIL_RECOVERY_MIN_GAP_RATIO = 0.08     # relative: at least 8 % of total
TAIL_RECOVERY_OVERLAP_SECONDS = 1.5    # overlap with last segment to avoid cuts


class TranscriptionWorker(QObject):
    """
    Runs inside a background QThread via moveToThread().
    Signals are the ONLY thread-safe way to communicate back to the UI.

    Flow:
      1. run() is called by QThread.started signal
      2. get_model() returns (or loads) the singleton WhisperModel
      3. model.transcribe() returns a lazy generator — we iterate it
      4. For each segment: emit segment_ready + progress_updated
      5. When generator exhausted: emit finished with the full result dict
    """

    # One segment dict emitted as soon as it is decoded
    segment_ready = Signal(dict)

    # (seconds_done, total_seconds) — used to drive the progress bar
    progress_updated = Signal(float, float)

    # Full result dict when all segments are done
    finished = Signal(dict)

    error = Signal(str)

    # Emitted if cancelled early
    cancelled = Signal()

    # Emitted once the model is loaded and transcription actually starts
    # (so the UI can switch from indeterminate → percentage progress bar)
    transcription_started = Signal(float)   # payload = total_duration

    def __init__(
        self,
        file_path: str,
        audio_language: str | None = None,
        initial_prompt: str = "",
        beam_size: int = 5,
        profile: str = "balanced",
    ):
        super().__init__()
        self.file_path = file_path
        self.audio_language = audio_language
        self.initial_prompt = initial_prompt
        self.beam_size = beam_size
        self.profile = profile if profile in TRANSCRIPTION_PROFILES else "balanced"
        self._cancelled = False

    def cancel(self):
        """Called from the UI thread to request cancellation."""
        self._cancelled = True

    def _segment_to_dict(self, seg) -> dict:
        return {
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": [
                {
                    "word": w.word,
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                }
                for w in (seg.words or [])
            ],
        }

    def _build_transcribe_kwargs(self, profile_name: str) -> dict:
        profile_cfg = TRANSCRIPTION_PROFILES[profile_name]
        kwargs = {
            "language": self.audio_language,
            "word_timestamps": True,
            "vad_filter": profile_cfg["vad_filter"],
            "vad_parameters": profile_cfg["vad_parameters"],
            "beam_size": self.beam_size,
            "no_repeat_ngram_size": 3,
            "condition_on_previous_text": False,
            "no_speech_threshold": profile_cfg["no_speech_threshold"],
            "log_prob_threshold": profile_cfg["log_prob_threshold"],
            "compression_ratio_threshold": profile_cfg["compression_ratio_threshold"],
            "hallucination_silence_threshold": profile_cfg["hallucination_silence_threshold"],
        }
        if self.initial_prompt:
            kwargs["initial_prompt"] = self.initial_prompt
        return kwargs

    def _is_missing_vad_asset_error(self, exc: Exception) -> bool:
        """Detect missing Silero VAD asset errors in packaged environments."""
        text = str(exc).lower()
        return (
            "silero_vad_v6.onnx" in text
            or ("no_suchfile" in text and "vad" in text)
        )

    def _should_attempt_tail_recovery(
        self,
        total_duration: float,
        last_end: float,
        active_profile: str,
    ) -> bool:
        if active_profile == "no_vad" or total_duration <= 0:
            return False
        tail_gap = max(0.0, total_duration - last_end)
        return (
            tail_gap >= TAIL_RECOVERY_MIN_GAP_SECONDS
            and (tail_gap / total_duration) >= TAIL_RECOVERY_MIN_GAP_RATIO
        )

    def _recover_tail_segments(self, model, last_end: float, total_duration: float) -> list[dict]:
        fallback_profile = "no_vad"
        clip_start = max(0.0, last_end - TAIL_RECOVERY_OVERLAP_SECONDS)
        fallback_kwargs = self._build_transcribe_kwargs(fallback_profile)
        fallback_kwargs["clip_timestamps"] = [clip_start, total_duration]

        logging.warning(
            "Tail recovery started: profile=%s, clip=[%.3f, %.3f], gap=%.3f",
            fallback_profile,
            clip_start,
            total_duration,
            max(0.0, total_duration - last_end),
        )

        recovered: list[dict] = []
        tail_gen, _ = model.transcribe(self.file_path, **fallback_kwargs)
        for seg in tail_gen:
            if self._cancelled:
                return []
            seg_dict = self._segment_to_dict(seg)
            if seg_dict["end"] <= last_end + 0.2:
                continue
            recovered.append(seg_dict)
        return recovered

    # ------------------------------------------------------------------
    def run(self):
        try:
            # ── 1. Load / reuse the model singleton ───────────────────
            # On first call this blocks while downloading/loading (~3 GB).
            # Subsequent calls return instantly from the module-level cache.
            model = get_model()

            if self._cancelled:
                self.cancelled.emit()
                return

            # ── 2. Start transcription (returns a lazy generator) ──────
            active_profile = self.profile
            transcribe_kwargs = self._build_transcribe_kwargs(active_profile)

            logging.info(
                "Transcription profile=%s, vad=%s, beam=%s, lang=%s",
                active_profile,
                transcribe_kwargs["vad_filter"],
                self.beam_size,
                self.audio_language,
            )

            try:
                segments_gen, info = model.transcribe(self.file_path, **transcribe_kwargs)
            except Exception as exc:
                if transcribe_kwargs.get("vad_filter") and self._is_missing_vad_asset_error(exc):
                    logging.warning(
                        "Silero VAD asset missing in packaged app; retrying transcription with no_vad profile."
                    )
                    active_profile = "no_vad"
                    transcribe_kwargs = self._build_transcribe_kwargs(active_profile)
                    segments_gen, info = model.transcribe(self.file_path, **transcribe_kwargs)
                else:
                    raise

            total_duration: float = info.duration   # seconds
            duration_after_vad: float = float(
                getattr(info, "duration_after_vad", total_duration) or total_duration
            )
            self.transcription_started.emit(total_duration)

            # ── 3. Iterate segments, emitting each one live ────────────
            collected: list[dict] = []
            last_end = 0.0
            last_progress_second = -1
            word_count = 0

            for seg in segments_gen:
                if self._cancelled:
                    self.cancelled.emit()
                    return

                seg_dict = self._segment_to_dict(seg)
                collected.append(seg_dict)
                last_end = seg_dict["end"]
                word_count += len(seg_dict["text"].split())

                self.segment_ready.emit(seg_dict)
                progress_second = int(last_end)
                if progress_second != last_progress_second or last_end >= total_duration:
                    self.progress_updated.emit(last_end, total_duration)
                    last_progress_second = progress_second

            tail_gap = max(0.0, total_duration - last_end)
            diagnostics = {
                "profile": active_profile,
                "requested_profile": self.profile,
                "vad_enabled": bool(transcribe_kwargs["vad_filter"]),
                "duration_after_vad_seconds": round(duration_after_vad, 3),
                "vad_removed_seconds": round(max(0.0, total_duration - duration_after_vad), 3),
                "tail_gap_seconds": round(tail_gap, 3),
                "tail_recovery_attempted": False,
                "tail_recovered_segments": 0,
            }

            if self._should_attempt_tail_recovery(total_duration, last_end, active_profile):
                diagnostics["tail_recovery_attempted"] = True
                recovered_segments = self._recover_tail_segments(model, last_end, total_duration)

                if recovered_segments:
                    for seg_dict in recovered_segments:
                        collected.append(seg_dict)
                        word_count += len(seg_dict["text"].split())
                        self.segment_ready.emit(seg_dict)

                    collected.sort(key=lambda s: (s["start"], s["end"]))
                    last_end = max(last_end, max(seg["end"] for seg in recovered_segments))
                    diagnostics["tail_recovered_segments"] = len(recovered_segments)
                    diagnostics["tail_gap_seconds"] = round(
                        max(0.0, total_duration - last_end),
                        3,
                    )
                    self.progress_updated.emit(last_end, total_duration)

                    logging.warning(
                        "Tail recovery added %s segments, remaining_gap=%.3fs",
                        len(recovered_segments),
                        max(0.0, total_duration - last_end),
                    )
                else:
                    logging.warning("Tail recovery attempted but found no additional speech segments.")

            # ── 4. Build and emit the final result ─────────────────────
            if not self._cancelled:
                result = {
                    "duration_seconds":    total_duration,
                    "language":            info.language,
                    "language_confidence": info.language_probability,
                    "word_count":          word_count,
                    "segments": collected,
                    "model":    "large-v3",
                    "transcription_diagnostics": diagnostics,
                }
                self.finished.emit(result)

        except Exception as exc:
            logging.error("Transcription error: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ──────────────────────────────────────────────────────────────────────
class ModelPreloadWorker(QObject):
    """Warm the model singleton in a background thread at app startup.

    Called from ``MainWindow._start_model_preload()``.  By the time
    this worker runs, ``main.py`` has already confirmed that the model
    binary exists on disk (via the setup dialog).  This worker calls
    ``get_model()`` which loads the ~3 GB file into RAM (or VRAM)
    so that the first transcription starts instantly.

    If loading fails (corrupted model, missing DLLs), the ``error``
    signal carries the message to ``MainWindow._on_preload_error()``
    which shows a dialog with recovery steps.
    """
    status  = Signal(str)   # human-readable status text
    finished = Signal()
    error    = Signal(str)

    def run(self):
        try:
            self.status.emit("Loading Whisper engine into memory…")
            get_model()          # warms the singleton
            self.status.emit("Model ready.")
            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
