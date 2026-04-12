import argostranslate.package
import argostranslate.translate
from PySide6.QtCore import QObject, Signal

class TranslationWorker(QObject):
    finished = Signal(list)     # list of translated segment dicts
    error = Signal(str)
    progress = Signal(int, int) # (current_segment, total_segments)

    def __init__(self, segments: list, from_code: str, to_code: str):
        super().__init__()
        self.segments = segments
        self.from_code = from_code
        self.to_code = to_code

    def run(self):
        try:
            # Get installed translation
            installed = argostranslate.translate.get_installed_languages()
            from_lang = next(
                (l for l in installed if l.code == self.from_code), None
            )
            to_lang = next(
                (l for l in installed if l.code == self.to_code), None
            )
            
            if not from_lang or not to_lang:
                self.error.emit(
                    f"Translation model {self.from_code}â†’{self.to_code} not installed."
                )
                return
            
            translation = from_lang.get_translation(to_lang)
            result = []
            total = len(self.segments)
            
            for i, seg in enumerate(self.segments):
                translated_text = translation.translate(seg["text"])
                result.append({**seg, "text": translated_text})
                self.progress.emit(i + 1, total)
            
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class PackageInstallWorker(QObject):
    """Downloads + installs an Argos language model."""
    finished = Signal()
    error = Signal(str)
    status = Signal(str)

    def __init__(self, from_code: str, to_code: str):
        super().__init__()
        self.from_code = from_code
        self.to_code = to_code

    def run(self):
        try:
            self.status.emit("Fetching package index...")
            argostranslate.package.update_package_index()
            
            available = argostranslate.package.get_available_packages()
            pkg = next(
                (p for p in available
                 if p.from_code == self.from_code and p.to_code == self.to_code),
                None
            )
            
            if not pkg:
                self.error.emit(
                    f"No direct model found for {self.from_code} â†’ {self.to_code}."
                )
                return
            
            self.status.emit("Downloading model (~100-150 MB)...")
            download_path = pkg.download()
            
            self.status.emit("Installing model...")
            argostranslate.package.install_from_path(download_path)
            
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


def get_installed_language_pairs() -> list[tuple[str, str]]:
    """Returns list of (from_code, to_code) tuples for installed models."""
    try:
        installed = argostranslate.translate.get_installed_languages()
        pairs = []
        for lang in installed:
            for t in lang.translations_from:
                pairs.append((lang.code, t.to_lang.code))
        return pairs
    except Exception:
        return []


def get_available_language_pairs() -> list[dict]:
    """
    Returns list of dicts describing available packages from the index.
    Each dict: { from_code, to_code, from_name, to_name, installed: bool }
    Note: requires internet access once to fetch the index.
    """
    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()
    installed_pairs = get_installed_language_pairs()
    result = []
    
    # Track what we've added to avoid duplicates if argostranslate lists them multiple times
    added_pairs = set()
    
    for pkg in available:
        pair_key = (pkg.from_code, pkg.to_code)
        if pair_key not in added_pairs:
            result.append({
                "from_code": pkg.from_code,
                "to_code": pkg.to_code,
                "from_name": pkg.from_name,
                "to_name": pkg.to_name,
                "installed": pair_key in installed_pairs
            })
            added_pairs.add(pair_key)
    return result
