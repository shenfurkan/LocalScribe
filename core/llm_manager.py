import os
import sys
import logging
from threading import Lock
from PySide6.QtCore import QObject, Signal
from huggingface_hub import hf_hub_download
from core.paths import models_dir

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None

# ── Module-level state ──
_llm = None
_llm_lock = Lock()

# Replace with the exact HuggingFace repo once Gemma 3 GGUFs are officially pinned.
# We'll default to a 4B parameter Q4_K_M for an excellent balance of speed/quality.
DEFAULT_REPO = "bartowski/gemma-3-4b-it-GGUF"
DEFAULT_FILE = "gemma-3-4b-it-Q4_K_M.gguf"


def _resolve_download_root() -> str:
    return str(models_dir())


def get_llm(repo_id: str = DEFAULT_REPO, filename: str = DEFAULT_FILE, progress_callback=None) -> Llama:
    """
    Downloads (if necessary) and loads the LLM singleton into memory.
    """
    if Llama is None:
        raise RuntimeError(
            "llama-cpp-python is not installed. "
            "Install it to enable AI Assistant: python -m pip install llama-cpp-python"
        )

    global _llm
    if _llm is None:
        with _llm_lock:
            if _llm is None:
                root = _resolve_download_root()
                os.makedirs(root, exist_ok=True)
                
                logging.info(f"Checking for LLM model: {filename} in {root}")
                if progress_callback:
                    progress_callback.emit(f"Downloading/Locating model: {filename}...")
                
                try:
                    # Download or return cached path from HF
                    model_path = hf_hub_download(
                        repo_id=repo_id,
                        filename=filename,
                        cache_dir=root,
                        local_dir=root, # Places the file directly in our models folder
                        local_dir_use_symlinks=False
                    )
                    
                    if progress_callback:
                        progress_callback.emit("Loading LLM into memory...")
                        
                    # Initialize the llama-cpp-python context
                    # Enable n_gpu_layers=0 since we rely heavily on CPU portability here, 
                    # but it scales to GPU if the library is compiled with CUBLAS.
                    _llm = Llama(
                        model_path=model_path,
                        n_ctx=4096,           # Context window (4K is solid for transcripts)
                        n_threads=4,          # CPU threads
                        n_gpu_layers=0,       # Force CPU by default for stability
                        verbose=False         # Disable massive terminal spam
                    )
                    logging.info("LLM loaded successfully.")
                except Exception as e:
                    logging.error(f"Failed to load LLM: {str(e)}")
                    raise e
    return _llm


class LLMWorker(QObject):
    """
    Background worker that runs the LLM generation so the UI doesn't freeze.
    Yields string tokens one by one for a typewriter effect.
    """
    token_yielded = Signal(str)
    finished = Signal()
    error = Signal(str)
    status_update = Signal(str)

    def __init__(self, system_prompt: str, user_prompt: str):
        super().__init__()
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            # 1. Ensure Model is Loaded
            llm = get_llm(progress_callback=self.status_update)
            
            if self._cancelled:
                self.finished.emit()
                return

            # Gemma 3 specific chat formatting structure
            # Adjust if the prompt template varies
            prompt = (
                f"<start_of_turn>user\n{self.system_prompt}\n\n{self.user_prompt}<end_of_turn>\n"
                f"<start_of_turn>model\n"
            )

            self.status_update.emit("Generating response...")
            
            # 2. Run Inference Iterator
            stream = llm(
                prompt,
                max_tokens=1024,
                temperature=0.3, # Low temp for factual summaries
                stream=True
            )

            for output in stream:
                if self._cancelled:
                    break
                token = output["choices"][0]["text"]
                self.token_yielded.emit(token)

            self.finished.emit()

        except Exception as e:
            self.error.emit(str(e))
