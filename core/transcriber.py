import logging
from PySide6.QtCore import QObject, Signal
from core.model_manager import get_model


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

    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self._cancelled = False

    def cancel(self):
        """Called from the UI thread to request cancellation."""
        self._cancelled = True

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
            segments_gen, info = model.transcribe(
                self.file_path,
                word_timestamps=True,       # word-level start/end times
                vad_filter=True,            # skip silent passages (VAD)
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=200,
                ),
                beam_size=5,                # higher → more accurate but slower
                no_repeat_ngram_size=3,     # suppress hallucinated repetitions
                temperature=0.0,            # deterministic decoding
            )

            total_duration: float = info.duration   # seconds
            self.transcription_started.emit(total_duration)

            # ── 3. Iterate segments, emitting each one live ────────────
            collected: list[dict] = []
            last_end = 0.0

            for seg in segments_gen:
                if self._cancelled:
                    self.cancelled.emit()
                    return

                seg_dict = {
                    "start": round(seg.start, 3),
                    "end":   round(seg.end,   3),
                    "text":  seg.text.strip(),
                    "words": [
                        {
                            "word":  w.word,
                            "start": round(w.start, 3),
                            "end":   round(w.end,   3),
                        }
                        for w in (seg.words or [])
                    ],
                }
                collected.append(seg_dict)
                last_end = seg.end

                self.segment_ready.emit(seg_dict)
                self.progress_updated.emit(last_end, total_duration)

            # ── 4. Build and emit the final result ─────────────────────
            if not self._cancelled:
                result = {
                    "duration_seconds":    total_duration,
                    "language":            info.language,
                    "language_confidence": info.language_probability,
                    "word_count":          sum(
                        len(s["text"].split()) for s in collected
                    ),
                    "segments": collected,
                    "model":    "large-v3",
                }
                self.finished.emit(result)

        except Exception as exc:
            logging.error(f"Transcription error: {exc}", exc_info=True)
            self.error.emit(str(exc))


# ──────────────────────────────────────────────────────────────────────
class ModelPreloadWorker(QObject):
    """
    Pre-loads the model in the background at app startup so that the
    first transcription starts instantly.

    Connect to the status signal to update a splash / loading label.
    """
    status  = Signal(str)   # human-readable status text
    finished = Signal()
    error    = Signal(str)

    def run(self):
        try:
            self.status.emit("Loading model (large-v3) — first run downloads ~3 GB…")
            get_model()          # warms the singleton
            self.status.emit("Model ready.")
            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
