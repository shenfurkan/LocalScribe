from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QFrame,
    QLineEdit, QSpinBox, QScrollArea, QWidget
)
from PySide6.QtCore import Qt


# A curated list of popular languages supported by Whisper
# "Auto-Detect" passes None to faster-whisper.
WHISPER_LANGUAGES = {
    "Auto-Detect": None,
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Italian": "it",
    "Portuguese": "pt",
    "Dutch": "nl",
    "Russian": "ru",
    "Japanese": "ja",
    "Chinese": "zh",
    "Korean": "ko",
    "Arabic": "ar",
    "Hindi": "hi",
    "Turkish": "tr",
    "Polish": "pl",
    "Indonesian": "id",
    "Vietnamese": "vi"
}


class LanguageDialog(QDialog):
    def __init__(self, file_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced Transcription Settings")
        self.setFixedWidth(420)
        self.setModal(True)

        self.selected_code = None
        self.initial_prompt = ""
        self.beam_size = 5

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # ── Language selection ─────────────────────────────────────────
        header = QLabel("Audio Language")
        header.setObjectName("FieldLabel")
        layout.addWidget(header)

        desc = QLabel(f"Select the spoken language in <b>{file_name}</b>, or leave it to Auto-Detect.")
        desc.setObjectName("SentenceDesc")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.combo = QComboBox()
        self.combo.addItems(list(WHISPER_LANGUAGES.keys()))
        layout.addWidget(self.combo)

        # ── Initial Prompt ─────────────────────────────────────────────
        prompt_header = QLabel("Context Prompt (Optional)")
        prompt_header.setObjectName("FieldLabel")
        layout.addWidget(prompt_header)
        
        prompt_desc = QLabel("Provide names, acronyms, or context to improve spelling accuracy for complex words.")
        prompt_desc.setObjectName("SentenceDesc")
        prompt_desc.setWordWrap(True)
        layout.addWidget(prompt_desc)
        
        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText("e.g. LocalScribe, SaaS, API, PySide6...")
        layout.addWidget(self.prompt_input)

        # ── Beam Size ──────────────────────────────────────────────────
        beam_header = QLabel("Beam Size Search")
        beam_header.setObjectName("FieldLabel")
        layout.addWidget(beam_header)
        
        beam_desc = QLabel("Higher values (e.g. 10 or 15) slightly increase accuracy on difficult audio but take more processing time. 5 is recommended.")
        beam_desc.setObjectName("SentenceDesc")
        beam_desc.setWordWrap(True)
        layout.addWidget(beam_desc)
        
        self.beam_spinner = QSpinBox()
        self.beam_spinner.setRange(1, 30)
        self.beam_spinner.setValue(5)
        layout.addWidget(self.beam_spinner)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("SectionSeparator")
        layout.addWidget(sep)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        start_btn = QPushButton("Start Transcription")
        start_btn.setObjectName("PrimaryBtn")
        start_btn.clicked.connect(self._on_start)

        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(start_btn)
        
        layout.addLayout(btn_layout)

    def _on_start(self):
        selection = self.combo.currentText()
        self.selected_code = WHISPER_LANGUAGES.get(selection)
        self.initial_prompt = self.prompt_input.text().strip()
        self.beam_size = self.beam_spinner.value()
        self.accept()
