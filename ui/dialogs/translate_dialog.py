from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QProgressBar
)
from PySide6.QtCore import Signal, QThread
from core.translator import (
    TranslationWorker, PackageInstallWorker,
    get_installed_language_pairs, get_available_language_pairs
)
from core.storage import StorageManager

class TranslateDialog(QDialog):
    translation_saved = Signal(dict)   # emits updated transcript

    def __init__(self, transcript: dict, storage: StorageManager, parent=None):
        super().__init__(parent)
        self.transcript = transcript
        self.storage = storage
        self._pairs = []
        self.setWindowTitle("Translate Transcript")
        self.setMinimumWidth(460)
        
        layout = QVBoxLayout(self)
        
        # Source language (auto-detected)
        src_lang = transcript.get("language", "en")
        layout.addWidget(QLabel(f"Translating from: {src_lang.upper()}"))
        
        layout.addWidget(QLabel("Translate to:"))
        
        self.lang_combo = QComboBox()
        self.lang_combo.setEditable(False) # Keep false, list can be long
        layout.addWidget(self.lang_combo)
        
        self.install_note = QLabel("")
        self.install_note.setObjectName("InstallNote")
        layout.addWidget(self.install_note)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self.translate_btn = QPushButton("Translate")
        self.translate_btn.setObjectName("PrimaryBtn")
        self.translate_btn.clicked.connect(self._start)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self.translate_btn)
        layout.addLayout(btn_row)
        
        self._load_languages(src_lang)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)

    def _load_languages(self, src_lang: str):
        """Populate combo with installed + available language pairs."""
        self.status_label.setText("Checking offline translation pairs...")
        self.translate_btn.setEnabled(False)
        self.lang_combo.setEnabled(False)
        
        # Build the combo async to avoid freezing
        self._check_thread = QThread()
        self._check_worker = LanguageCheckWorker(src_lang)
        self._check_worker.moveToThread(self._check_thread)
        self._check_worker.finished.connect(self._on_languages_loaded)
        self._check_thread.started.connect(self._check_worker.run)
        
        # Cleanup
        self._check_worker.finished.connect(self._check_thread.quit)
        self._check_worker.finished.connect(self._check_worker.deleteLater)
        self._check_thread.finished.connect(self._check_thread.deleteLater)
        self._check_thread.start()

    def _on_languages_loaded(self, pairs):
        self._pairs = pairs
        for p in self._pairs:
            badge = "✅" if p["installed"] else "⬇️"
            self.lang_combo.addItem(
                f"{badge} {p['to_name']} ({p['to_code']})"
            )
        
        self.status_label.setText("")
        self.translate_btn.setEnabled(True)
        self.lang_combo.setEnabled(True)
        
        # Initial status update
        self._on_lang_changed(self.lang_combo.currentIndex())

    def _on_lang_changed(self, idx):
        if idx < 0 or idx >= len(self._pairs):
            return
        p = self._pairs[idx]
        if p["installed"]:
            self.install_note.setText("Ready to translate offline.")
        else:
            self.install_note.setText(
                f"⬇️ Model for {p['to_name']} will be downloaded (~100–150 MB) before translating."
            )

    def _start(self):
        idx = self.lang_combo.currentIndex()
        if idx < 0:
            return
        p = self._pairs[idx]
        
        self.translate_btn.setEnabled(False)
        self.lang_combo.setEnabled(False)
        
        if not p["installed"]:
            self._download_then_translate(p)
        else:
            self._run_translation(p["from_code"], p["to_code"])

    def _download_then_translate(self, pair: dict):
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.show()
        
        self._worker = PackageInstallWorker(pair["from_code"], pair["to_code"])
        self._install_thread = QThread()
        self._worker.moveToThread(self._install_thread)
        self._install_thread.started.connect(self._worker.run)
        self._worker.status.connect(self.status_label.setText)
        
        self._worker.finished.connect(
            lambda: self._run_translation(pair["from_code"], pair["to_code"])
        )
        self._worker.finished.connect(self._install_thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.error.connect(self._on_error)
        self._worker.error.connect(self._install_thread.quit)
        self._worker.error.connect(self._worker.deleteLater)
        self._install_thread.finished.connect(self._install_thread.deleteLater)
        self._install_thread.start()

    def _run_translation(self, from_code: str, to_code: str):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_label.setText("Translating...")
        
        self._trans_worker = TranslationWorker(
            self.transcript["segments"], from_code, to_code
        )
        self._trans_thread = QThread()
        self._trans_worker.moveToThread(self._trans_thread)
        self._trans_thread.started.connect(self._trans_worker.run)
        
        self._trans_worker.progress.connect(
            lambda cur, tot: self.progress_bar.setValue(int(cur / tot * 100))
        )
        
        self._trans_worker.finished.connect(self._on_translation_done)
        self._trans_worker.finished.connect(self._trans_thread.quit)
        self._trans_worker.finished.connect(self._trans_worker.deleteLater)
        self._trans_worker.error.connect(self._on_error)
        self._trans_worker.error.connect(self._trans_thread.quit)
        self._trans_worker.error.connect(self._trans_worker.deleteLater)
        self._trans_thread.finished.connect(self._trans_thread.deleteLater)
        self._trans_thread.start()

    def _on_error(self, err):
        self.status_label.setText(f"Error: {err}")
        self.translate_btn.setEnabled(True)
        self.progress_bar.hide()

    def _on_translation_done(self, translated_segments: list):
        to_code = self._pairs[self.lang_combo.currentIndex()]["to_code"]
        self.storage.save_translation(
            self.transcript["id"], to_code, translated_segments
        )
        updated = self.storage.load(self.transcript["id"])
        self.translation_saved.emit(updated)
        self.accept()


from PySide6.QtCore import QObject
class LanguageCheckWorker(QObject):
    finished = Signal(list)
    def __init__(self, src_lang):
        super().__init__()
        self.src_lang = src_lang
    def run(self):
        installed = set(get_installed_language_pairs())
        try:
            all_pairs = get_available_language_pairs()
        except Exception:
            all_pairs = [
                {"from_code": f, "to_code": t,
                 "to_name": t, "installed": True}
                for f, t in installed
            ]
        pairs = [
            p for p in all_pairs if p["from_code"] == self.src_lang
        ]
        self.finished.emit(pairs)
