from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QTextBrowser, QLineEdit, QProgressBar, QFrame, QMessageBox
)
from PySide6.QtCore import Qt, QThread
from core.llm_manager import LLMWorker
from core.exporter import export_txt


class AIDialog(QDialog):
    def __init__(self, transcript: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🪄 Local Assistant (Gemma 3)")
        self.setMinimumSize(600, 500)
        self.setModal(False) # Non-modal so they can look at the transcript

        # Generate a plain-text version of the transcript to feed to the LLM
        segs = transcript.get("segments", [])
        self.full_transcript_text = export_txt(segs, include_timestamps=False)
        self.file_name = transcript.get("name", "Document")

        self._active_thread = None
        self._active_worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # ── Header ──────────────────────────────────────────────────
        header = QLabel(f"Querying: <b>{self.file_name}</b>")
        header.setObjectName("FieldLabel")
        layout.addWidget(header)

        # ── Chat / Output Area ──────────────────────────────────────
        self.chat_display = QTextBrowser()
        self.chat_display.setObjectName("TranscriptEditor")
        self.chat_display.setPlaceholderText("Ask the AI to summarize this transcript, extract action items, or format it as an interview...")
        layout.addWidget(self.chat_display, stretch=1)

        # ── Status and Progress ──────────────────────────────────────
        self.status_label = QLabel("")
        self.status_label.setObjectName("SentenceDesc")
        self.status_label.hide()
        layout.addWidget(self.status_label)

        # ── Pre-set Action Buttons ──────────────────────────────────
        preset_layout = QHBoxLayout()
        preset_layout.setSpacing(8)

        btn_summary = QPushButton("✨ Generate Summary")
        btn_summary.clicked.connect(lambda: self._run_prompt("Summarize the following transcript in 3-4 bullet points, highlighting the core topics discussed."))
        
        btn_action = QPushButton("✅ Action Items")
        btn_action.clicked.connect(lambda: self._run_prompt("Read the following transcript and list any explicit or implied action items, tasks, or follow-ups mentioned."))

        btn_interview = QPushButton("🎙 Format as Interview")
        btn_interview.clicked.connect(lambda: self._run_prompt("Rewrite the following transcript strictly as an interview format with 'Interviewer:' and 'Guest:' labels."))

        preset_layout.addWidget(btn_summary)
        preset_layout.addWidget(btn_action)
        preset_layout.addWidget(btn_interview)
        layout.addLayout(preset_layout)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("SectionSeparator")
        layout.addWidget(sep)

        # ── Custom Input Area ───────────────────────────────────────
        input_layout = QHBoxLayout()
        
        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText("Ask a custom question about the transcript...")
        self.prompt_input.returnPressed.connect(self._run_custom_prompt)
        
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("PrimaryBtn")
        self.send_btn.clicked.connect(self._run_custom_prompt)
        
        self.cancel_btn = QPushButton("Stop")
        self.cancel_btn.clicked.connect(self._stop_generation)
        self.cancel_btn.hide()

        input_layout.addWidget(self.prompt_input, stretch=1)
        input_layout.addWidget(self.send_btn)
        input_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(input_layout)

    def _run_custom_prompt(self):
        query = self.prompt_input.text().strip()
        if query:
            self._run_prompt(query)
            self.prompt_input.clear()

    def _run_prompt(self, instruction: str):
        if self._active_thread and self._active_thread.isRunning():
            return
            
        self.chat_display.clear()
        
        # We append the actual transcript to the instruction as context
        system_prompt = (
            f"{instruction}\n\n"
            f"--- TRANSCRIPT CONTENT ---\n"
            f"{self.full_transcript_text}\n"
            f"--------------------------"
        )
        
        self._active_worker = LLMWorker(system_prompt=system_prompt, user_prompt=instruction)
        self._active_thread = QThread(self)
        self._active_worker.moveToThread(self._active_thread)

        self._active_thread.started.connect(self._active_worker.run)
        
        # Connect Signals
        self._active_worker.token_yielded.connect(self._on_token)
        self._active_worker.status_update.connect(self._update_status)
        self._active_worker.finished.connect(self._on_generation_finished)
        self._active_worker.error.connect(self._on_error)

        # UI State adjustments
        self.send_btn.hide()
        self.cancel_btn.show()
        self.status_label.show()
        
        self._active_thread.start()

    def _on_token(self, token: str):
        self.status_label.hide() # Hide status once tokens start arriving
        
        # Insert raw text continuously
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.End)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.insertPlainText(token)
        self.chat_display.ensureCursorVisible()

    def _update_status(self, msg: str):
        self.status_label.setText(f"<i>{msg}</i>")

    def _on_generation_finished(self):
        self.cancel_btn.hide()
        self.send_btn.show()
        self.status_label.hide()
        if self._active_thread:
            self._active_thread.quit()
            self._active_thread.wait()

    def _on_error(self, err: str):
        self._on_generation_finished()
        QMessageBox.critical(self, "AI Assistant Error", f"An error occurred: {err}\n\nMake sure your machine has enough RAM to load the model.")

    def _stop_generation(self):
        if self._active_worker:
            self._active_worker.cancel()

    def closeEvent(self, event):
        self._stop_generation()
        if self._active_thread:
            self._active_thread.quit()
            self._active_thread.wait()
        super().closeEvent(event)
