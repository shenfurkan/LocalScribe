"""ui/setup_dialog.py — First-run / model-management wizard.

Three-step wizard implemented as a ``QStackedWidget`` inside a ``QDialog``:

1. **Hugging Face token** (optional) — paste + validate + save, or skip.
2. **Model picker** — pick any Whisper model from the manifest. Models
   already downloaded are marked "Installed" and can be activated
   without re-downloading.
3. **Download** — live progress bar with %, MB/s and ETA. Skipped when
   the selected model is already installed.

The dialog exposes a ``setup_succeeded`` property and an ``active_model_id``
field once it closes successfully.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QLineEdit, QStackedWidget, QWidget, QRadioButton,
    QButtonGroup, QFrame, QScrollArea, QSizePolicy, QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt, QThread, Signal, QObject

from core.setup_manager import (
    SetupWorker,
    list_available_models,
    is_model_ready,
    get_default_model_id,
    get_active_model_id,
    get_model_entry,
    set_active_model_id,
    register_custom_model,
    save_hf_token,
    validate_hf_token,
    hf_token_path,
)


_DIALOG_QSS = """
QDialog#SetupDialog { background-color: #0f172a; border: 1px solid #1e293b; border-radius: 8px; }
QLabel#Title { font-family: 'Segoe UI', Arial, sans-serif; font-size: 15pt; font-weight: bold; color: #f8fafc; }
QLabel#Subtitle { font-size: 9pt; color: #94a3b8; }
QLabel#StepHint { font-size: 8pt; color: #64748b; }
QLabel#Percent { font-size: 16pt; font-weight: bold; color: #0ea5e9; }
QLabel#MetaLabel { font-size: 9pt; color: #cbd5e1; }
QLabel#ErrorLabel { color: #ef4444; font-size: 9pt; }
QLabel#SuccessLabel { color: #22c55e; font-size: 9pt; }
QProgressBar {
    border: 1px solid #334155; border-radius: 4px;
    background-color: #1e293b; text-align: center; color: transparent; height: 12px;
}
QProgressBar::chunk { background-color: #0ea5e9; border-radius: 3px; }
QPushButton {
    background-color: #1e293b; border: 1px solid #334155; border-radius: 4px;
    padding: 6px 18px; color: #f8fafc; font-weight: bold;
}
QPushButton:hover { background-color: #334155; }
QPushButton#PrimaryBtn { background-color: #0ea5e9; color: white; border: none; }
QPushButton#PrimaryBtn:hover { background-color: #0284c7; }
QPushButton#PrimaryBtn:disabled { background-color: #334155; color: #64748b; }
QLineEdit {
    background-color: #1e293b; border: 1px solid #334155; border-radius: 4px;
    padding: 6px 10px; color: #f8fafc; font-family: 'Consolas', monospace;
}
QFrame#ModelCard {
    background-color: #1e293b; border: 1px solid #334155; border-radius: 6px;
}
QFrame#ModelCard[selected="true"] { border: 1px solid #0ea5e9; background-color: #1e3a5f; }
QLabel#CardTitle { font-size: 11pt; font-weight: bold; color: #f8fafc; }
QLabel#CardMeta { font-size: 8pt; color: #94a3b8; }
QLabel#CardBadge {
    color: #22c55e; font-size: 8pt; font-weight: bold;
    background-color: rgba(34,197,94,0.12); border: 1px solid #22c55e;
    border-radius: 3px; padding: 2px 6px;
}
QLabel#CardBadgeDim {
    color: #94a3b8; font-size: 8pt;
    background-color: rgba(148,163,184,0.08); border: 1px solid #334155;
    border-radius: 3px; padding: 2px 6px;
}
QRadioButton { color: #f8fafc; font-weight: bold; }
"""


# ─────────────────────────────────────────────────────────────────────────
# Async token-validation worker (keeps UI responsive while waiting on HF)
# ─────────────────────────────────────────────────────────────────────────
class _TokenValidateWorker(QObject):
    finished = Signal(bool, str)  # (ok, username_or_error)

    def __init__(self, token: str):
        super().__init__()
        self._token = token

    def run(self):
        ok, msg = validate_hf_token(self._token)
        self.finished.emit(ok, msg)


# ─────────────────────────────────────────────────────────────────────────
# Model card widget — used in the picker page
# ─────────────────────────────────────────────────────────────────────────
class _ModelCard(QFrame):
    clicked = Signal(str)  # model_id

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("ModelCard")
        self.setProperty("selected", False)
        self._model_id = entry["id"]
        installed = is_model_ready(entry["id"])

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        self.radio = QRadioButton()
        self.radio.setFocusPolicy(Qt.NoFocus)
        row.addWidget(self.radio, 0, Qt.AlignVCenter)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel(entry["display_name"])
        title.setObjectName("CardTitle")
        title_row.addWidget(title)

        tier = QLabel(entry.get("tier", ""))
        tier.setObjectName("CardBadgeDim")
        title_row.addWidget(tier)

        badge = QLabel("Installed" if installed else "Not downloaded")
        badge.setObjectName("CardBadge" if installed else "CardBadgeDim")
        title_row.addWidget(badge)
        title_row.addStretch()
        info_col.addLayout(title_row)

        desc = QLabel(entry.get("description", ""))
        desc.setObjectName("CardMeta")
        desc.setWordWrap(True)
        info_col.addWidget(desc)

        size = entry.get("approx_size_mb")
        size_text = f"~{size} MB" if size else "size unknown"
        meta = QLabel(f"{size_text}  ·  repo: {entry.get('repo_id', '')}")
        meta.setObjectName("CardMeta")
        info_col.addWidget(meta)

        row.addLayout(info_col, 1)
        self.setCursor(Qt.PointingHandCursor)

    def set_selected(self, selected: bool):
        self.setProperty("selected", selected)
        self.radio.setChecked(selected)
        # Force style re-evaluation after property change.
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        self.clicked.emit(self._model_id)
        super().mousePressEvent(event)


# ─────────────────────────────────────────────────────────────────────────
# Main dialog
# ─────────────────────────────────────────────────────────────────────────
class SetupDialog(QDialog):
    """Three-step setup wizard. Use ``setup_succeeded`` after ``exec()``."""

    PAGE_TOKEN = 0
    PAGE_PICKER = 1
    PAGE_DOWNLOAD = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SetupDialog")
        self.setWindowTitle("LocalScribe Setup")
        self.setFixedSize(620, 520)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.CustomizeWindowHint)
        self.setStyleSheet(_DIALOG_QSS)

        self._success = False
        self.active_model_id: str | None = None

        # Worker/thread handles — populated when download starts.
        self._thread: QThread | None = None
        self._worker: SetupWorker | None = None

        # Token-validation worker (short-lived).
        self._tok_thread: QThread | None = None
        self._tok_worker: _TokenValidateWorker | None = None

        # Pre-selection defaults.
        self._selected_model_id: str = get_active_model_id() or get_default_model_id()
        self._pending_hf_token: str | None = None  # set if user entered a token

        # ── Root layout with a QStackedWidget ──────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        self.title_label = QLabel()
        self.title_label.setObjectName("Title")
        root.addWidget(self.title_label)

        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("Subtitle")
        self.subtitle_label.setWordWrap(True)
        root.addWidget(self.subtitle_label)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.stack.addWidget(self._build_token_page())
        self.stack.addWidget(self._build_picker_page())
        self.stack.addWidget(self._build_download_page())

        # Start on token page and render its header.
        self._go_to(self.PAGE_TOKEN)

    # ─────────────────────────────────────────────────────────────────
    # Page 1 — Hugging Face token (optional)
    # ─────────────────────────────────────────────────────────────────
    def _build_token_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        hint = QLabel(
            "A Hugging Face token raises your download rate limit (fewer "
            "throttling pauses on heavy networks) and unlocks gated models.\n"
            "It does not affect raw download speed — that is handled "
            "automatically by the Xet CDN acceleration.\n"
            "This step is optional."
        )
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        existing_label = QLabel()
        existing_label.setObjectName("StepHint")
        if hf_token_path().exists():
            existing_label.setText(f"A saved token was found at: {hf_token_path()}")
        else:
            existing_label.setText("No token saved yet.")
        layout.addWidget(existing_label)

        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self.token_edit.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.token_edit)

        row = QHBoxLayout()
        self.token_show_btn = QPushButton("Show")
        self.token_show_btn.setCheckable(True)
        self.token_show_btn.toggled.connect(self._toggle_token_visibility)
        row.addWidget(self.token_show_btn)
        self.token_validate_btn = QPushButton("Validate && Save")
        self.token_validate_btn.clicked.connect(self._on_validate_token_clicked)
        row.addWidget(self.token_validate_btn)
        row.addStretch()
        layout.addLayout(row)

        self.token_status_label = QLabel(" ")
        self.token_status_label.setObjectName("StepHint")
        self.token_status_label.setWordWrap(True)
        layout.addWidget(self.token_status_label)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.token_skip_btn = QPushButton("Skip")
        self.token_skip_btn.clicked.connect(lambda: self._go_to(self.PAGE_PICKER))
        btn_row.addWidget(self.token_skip_btn)
        self.token_next_btn = QPushButton("Continue")
        self.token_next_btn.setObjectName("PrimaryBtn")
        self.token_next_btn.clicked.connect(lambda: self._go_to(self.PAGE_PICKER))
        btn_row.addWidget(self.token_next_btn)
        layout.addLayout(btn_row)
        return page

    def _toggle_token_visibility(self, checked: bool):
        self.token_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self.token_show_btn.setText("Hide" if checked else "Show")

    def _on_validate_token_clicked(self):
        token = self.token_edit.text().strip()
        if not token:
            self.token_status_label.setText("Enter a token first, or press Skip.")
            self.token_status_label.setObjectName("ErrorLabel")
            self.token_status_label.setStyleSheet("")
            return

        self.token_validate_btn.setEnabled(False)
        self.token_skip_btn.setEnabled(False)
        self.token_next_btn.setEnabled(False)
        self.token_status_label.setObjectName("StepHint")
        self.token_status_label.setText("Validating token with Hugging Face…")

        self._tok_thread = QThread(self)
        self._tok_worker = _TokenValidateWorker(token)
        self._tok_worker.moveToThread(self._tok_thread)
        self._tok_thread.started.connect(self._tok_worker.run)
        self._tok_worker.finished.connect(self._on_token_validated)
        self._tok_worker.finished.connect(self._tok_thread.quit)
        self._tok_worker.finished.connect(self._tok_worker.deleteLater)
        self._tok_thread.finished.connect(self._tok_thread.deleteLater)
        self._tok_thread.start()

    def _on_token_validated(self, ok: bool, msg: str):
        self.token_validate_btn.setEnabled(True)
        self.token_skip_btn.setEnabled(True)
        self.token_next_btn.setEnabled(True)
        if ok:
            token = self.token_edit.text().strip()
            try:
                save_hf_token(token)
                self._pending_hf_token = token
                self.token_status_label.setObjectName("SuccessLabel")
                self.token_status_label.setText(f"Token valid ({msg}). Saved locally.")
            except Exception as exc:
                self.token_status_label.setObjectName("ErrorLabel")
                self.token_status_label.setText(f"Could not save token: {exc}")
        else:
            self.token_status_label.setObjectName("ErrorLabel")
            self.token_status_label.setText(f"Token rejected: {msg}")
        # Force re-style after changing object name.
        self.token_status_label.style().unpolish(self.token_status_label)
        self.token_status_label.style().polish(self.token_status_label)

    # ─────────────────────────────────────────────────────────────────
    # Page 2 — Model picker
    # ─────────────────────────────────────────────────────────────────
    def _build_picker_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        hint = QLabel(
            "Pick a Whisper model. Already-installed models are reused "
            "immediately — no re-download needed."
        )
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Scrollable list of cards so the picker remains compact.
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        container = QWidget()
        self._card_layout = QVBoxLayout(container)
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.setSpacing(8)

        self._cards: dict[str, _ModelCard] = {}
        self._picker_group = QButtonGroup(self)
        self._picker_group.setExclusive(True)

        for entry in list_available_models():
            card = _ModelCard(entry)
            card.clicked.connect(self._on_card_clicked)
            self._card_layout.addWidget(card)
            self._picker_group.addButton(card.radio)
            self._cards[entry["id"]] = card

        self._card_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        back_btn = QPushButton("Back")
        back_btn.clicked.connect(lambda: self._go_to(self.PAGE_TOKEN))
        btn_row.addWidget(back_btn)
        self.picker_import_btn = QPushButton("Import local folder…")
        self.picker_import_btn.clicked.connect(self._on_import_local_folder)
        btn_row.addWidget(self.picker_import_btn)
        btn_row.addStretch()
        self.picker_continue_btn = QPushButton("Continue")
        self.picker_continue_btn.setObjectName("PrimaryBtn")
        self.picker_continue_btn.clicked.connect(self._on_picker_continue)
        btn_row.addWidget(self.picker_continue_btn)
        layout.addLayout(btn_row)

        # Default selection — must happen after picker_continue_btn exists
        # so _apply_card_selection can update its label.
        self._apply_card_selection(self._selected_model_id)
        return page

    def _on_card_clicked(self, model_id: str):
        self._apply_card_selection(model_id)

    def _apply_card_selection(self, model_id: str):
        if model_id not in self._cards:
            model_id = next(iter(self._cards), "")
        self._selected_model_id = model_id
        for mid, card in self._cards.items():
            card.set_selected(mid == model_id)
        # Update Continue label based on installed state.
        if model_id and is_model_ready(model_id):
            self.picker_continue_btn.setText("Use this model")
        else:
            self.picker_continue_btn.setText("Download & Continue")

    def _on_import_local_folder(self):
        """Open a folder picker and register the folder as a custom model."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select a Whisper model folder (must contain model.bin + config.json)",
        )
        if not folder:
            return
        try:
            entry = register_custom_model(folder)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Invalid model folder",
                f"Could not use this folder as a Whisper model:\n\n{exc}",
            )
            return

        # Refresh picker to show the newly imported card and select it.
        self._selected_model_id = entry["id"]
        self._rebuild_picker_cards()

    def _on_picker_continue(self):
        model_id = self._selected_model_id
        if not model_id:
            return

        entry = get_model_entry(model_id) or {}

        # Custom local folders are never downloadable from HF. If a custom
        # entry is not currently ready, report the issue and keep the user
        # on the picker page instead of entering the download flow.
        if entry.get("custom") and not is_model_ready(model_id):
            model_path = entry.get("abs_path") or entry.get("local_dir_name") or "(unknown)"
            QMessageBox.critical(
                self,
                "Local model is not usable",
                "The imported local folder is currently missing or incomplete.\n\n"
                f"Model folder: {model_path}\n\n"
                "Re-import a valid CTranslate2 Whisper folder that contains "
                "at least model.bin and config.json.",
            )
            return

        if is_model_ready(model_id):
            # No download needed — just activate and finish.
            try:
                set_active_model_id(model_id)
                self.active_model_id = model_id
                self._success = True
                self.accept()
            except Exception as exc:
                self._show_download_error(f"Could not activate model: {exc}")
            return
        # Download needed — advance to page 3 and start worker.
        self._go_to(self.PAGE_DOWNLOAD)
        self._start_download(model_id)

    # ─────────────────────────────────────────────────────────────────
    # Page 3 — Download
    # ─────────────────────────────────────────────────────────────────
    def _build_download_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.dl_status_label = QLabel("Preparing…")
        self.dl_status_label.setObjectName("Subtitle")
        self.dl_status_label.setWordWrap(True)
        layout.addWidget(self.dl_status_label)

        layout.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        meta_row = QHBoxLayout()
        self.file_label = QLabel("")
        self.file_label.setObjectName("MetaLabel")
        meta_row.addWidget(self.file_label)
        meta_row.addStretch()
        self.speed_label = QLabel("")
        self.speed_label.setObjectName("MetaLabel")
        meta_row.addWidget(self.speed_label)
        self.eta_label = QLabel("")
        self.eta_label.setObjectName("MetaLabel")
        meta_row.addWidget(self.eta_label)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("Percent")
        meta_row.addWidget(self.percent_label)
        layout.addLayout(meta_row)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.dl_action_btn = QPushButton("Cancel")
        self.dl_action_btn.clicked.connect(self._on_dl_action_clicked)
        btn_row.addWidget(self.dl_action_btn)
        layout.addLayout(btn_row)

        return page

    def _start_download(self, model_id: str):
        self.progress_bar.setRange(0, 0)  # indeterminate until first chunk
        self.progress_bar.setValue(0)
        self.percent_label.setText("--")
        self.speed_label.setText("")
        self.eta_label.setText("")
        self.file_label.setText("")
        self.error_label.setText("")
        self.dl_action_btn.setText("Cancel")
        self.dl_action_btn.setEnabled(True)
        self.dl_action_btn.setObjectName("")
        self.dl_action_btn.setStyleSheet("")

        self._thread = QThread(self)
        self._worker = SetupWorker(model_id=model_id, hf_token=self._pending_hf_token)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)

        self._worker.status_update.connect(self.dl_status_label.setText)
        self._worker.file_status.connect(self.file_label.setText)
        self._worker.progress.connect(self._on_progress)
        self._worker.speed_update.connect(self._on_speed)
        self._worker.finished.connect(self._on_setup_finished)

        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def _on_progress(self, value: int):
        if value < 0:
            self.progress_bar.setRange(0, 0)
            self.percent_label.setText("--")
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(value)
            self.percent_label.setText(f"{value}%")

    def _on_speed(self, speed_text: str, eta_text: str):
        self.speed_label.setText(speed_text)
        self.eta_label.setText(f"ETA {eta_text}")

    def _on_dl_action_clicked(self):
        # If a worker is still running → cancel. Otherwise treat button as Continue/Close.
        if self._thread is not None and self._thread.isRunning() and self._worker is not None:
            self.dl_action_btn.setEnabled(False)
            self._worker.request_cancel()
            return
        # Worker already finished: either success → accept, or error → close.
        if self._success:
            self.accept()
        else:
            self.reject()

    def _on_setup_finished(self, success: bool, error_msg: str):
        self._success = success
        self.dl_action_btn.setEnabled(True)
        if success:
            self.active_model_id = self._selected_model_id
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
            self.percent_label.setText("100%")
            self.dl_status_label.setText("Ready to go!")
            self.dl_action_btn.setText("Continue")
            self.dl_action_btn.setObjectName("PrimaryBtn")
            self.style().unpolish(self.dl_action_btn)
            self.style().polish(self.dl_action_btn)
        else:
            self._show_download_error(error_msg or "Setup failed.")

    def _show_download_error(self, msg: str):
        self.dl_status_label.setText("Setup Failed")
        self.error_label.setText(msg)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.dl_action_btn.setText("Close")

    # ─────────────────────────────────────────────────────────────────
    # Navigation helpers
    # ─────────────────────────────────────────────────────────────────
    def _go_to(self, page: int):
        self.stack.setCurrentIndex(page)
        if page == self.PAGE_TOKEN:
            self.title_label.setText("Step 1 · Hugging Face token (optional)")
            self.subtitle_label.setText(
                "Paste a token to raise rate limits and access gated models, or skip."
            )
        elif page == self.PAGE_PICKER:
            # Refresh "Installed" badges in case the user just downloaded one.
            self._rebuild_picker_cards()
            self.title_label.setText("Step 2 · Choose a model")
            self.subtitle_label.setText(
                "Already-installed models are reused instantly. You can switch later."
            )
        elif page == self.PAGE_DOWNLOAD:
            self.title_label.setText("Step 3 · Downloading model")
            self.subtitle_label.setText(
                "Live progress, speed and estimated time remaining."
            )

    def _rebuild_picker_cards(self):
        """Rebuild the cards to reflect the current installed state."""
        # Clear existing cards.
        while self._card_layout.count():
            item = self._card_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()
        for btn in list(self._picker_group.buttons()):
            self._picker_group.removeButton(btn)
        # Re-add with fresh badges.
        for entry in list_available_models():
            card = _ModelCard(entry)
            card.clicked.connect(self._on_card_clicked)
            self._card_layout.addWidget(card)
            self._picker_group.addButton(card.radio)
            self._cards[entry["id"]] = card
        self._card_layout.addStretch()
        self._apply_card_selection(self._selected_model_id)

    # ─────────────────────────────────────────────────────────────────
    # Dialog lifecycle
    # ─────────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        # If a download worker is running, request cancellation and wait
        # for it to exit cleanly before letting the dialog close.
        if self._thread is not None and self._thread.isRunning() and self._worker is not None:
            self.dl_action_btn.setEnabled(False)
            self._worker.request_cancel()
            event.ignore()
            return
        super().closeEvent(event)

    # Public flags ----------------------------------------------------
    @property
    def setup_succeeded(self) -> bool:
        return self._success
