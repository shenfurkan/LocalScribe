from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy
from PySide6.QtCore import Signal, Qt


class TranscriptCard(QFrame):
    clicked = Signal()

    def __init__(self, transcript_data: dict):
        super().__init__()
        self.setObjectName("TranscriptCard")
        self.transcript_id = transcript_data["id"]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        # ── Name ──────────────────────────────────────────────────────────
        name_label = QLabel(transcript_data.get("name", "Unknown File"))
        name_label.setObjectName("CardName")
        name_label.setWordWrap(True)
        name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        # ── Language + duration row ────────────────────────────────────────
        lang = str(transcript_data.get("language", "?")).upper()
        dur  = transcript_data.get("duration_seconds", 0) or 0
        mins, secs = int(dur // 60), int(dur % 60)

        lang_lbl = QLabel(lang)
        lang_lbl.setObjectName("CardMeta")

        time_lbl = QLabel(f"{mins}:{secs:02d}")
        time_lbl.setObjectName("CardMeta")

        details_layout = QHBoxLayout()
        details_layout.addWidget(lang_lbl)
        details_layout.addStretch()
        details_layout.addWidget(time_lbl)

        # ── Status ─────────────────────────────────────────────────────────
        status = transcript_data.get("status", "ready")
        if status == "processing":
            status_lbl = QLabel("⏳ Processing…")
            status_lbl.setObjectName("CardStatusProcessing")
        elif status == "failed":
            status_lbl = QLabel("✗ Failed")
            status_lbl.setObjectName("CardStatusFailed")
        else:
            created = (transcript_data.get("created_at") or "").split("T")[0]
            status_lbl = QLabel(f"✓ {created}")
            status_lbl.setObjectName("CardStatusReady")

        layout.addWidget(name_label)
        layout.addLayout(details_layout)
        layout.addWidget(status_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
