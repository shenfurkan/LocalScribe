"""
ui/dialogs/srt_dialog.py

A dedicated dialog for exporting SRT subtitles with smart re-segmentation
using word-level timestamps produced by Whisper.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
    QDoubleSpinBox, QCheckBox, QPushButton, QFileDialog,
    QFrame, QToolButton, QSizePolicy, QWidget
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from core.exporter import resegment_for_srt, export_srt
import os


# ─────────────────────────────────────────────────────────────────────────────
# Small helper: a ① info label that wraps cleanly next to a heading
# ─────────────────────────────────────────────────────────────────────────────

def _info_icon() -> QLabel:
    lbl = QLabel("ⓘ")
    lbl.setObjectName("InfoIcon")
    lbl.setToolTip("")  # set by caller
    return lbl


def _field_row(text: str, tooltip: str) -> QHBoxLayout:
    """Returns an HBoxLayout with a bolded label + info icon."""
    row = QHBoxLayout()
    row.setSpacing(6)
    lbl = QLabel(text)
    lbl.setObjectName("FieldLabel")
    icon = _info_icon()
    icon.setToolTip(tooltip)
    row.addWidget(lbl)
    row.addWidget(icon)
    row.addStretch()
    return row


# ─────────────────────────────────────────────────────────────────────────────
class CollapsibleSection(QWidget):
    """
    A widget that shows a clickable header bar and hides/shows its content
    area when clicked. Works by animating the maximumHeight property.
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent)

        # Toggle button (the entire header row acts as a button)
        self._toggle_btn = QToolButton()
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(True)          # expanded = checked
        self._toggle_btn.setObjectName("CollapseBtn")
        self._toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._toggle_btn.setArrowType(Qt.UpArrow)
        self._toggle_btn.setText(f"  {title}")
        self._toggle_btn.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        self._toggle_btn.toggled.connect(self._on_toggled)

        # Separator line above header
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("SectionSeparator")

        # Content area
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 4, 0, 0)
        self._content_layout.setSpacing(10)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.addWidget(line)
        outer.addWidget(self._toggle_btn)
        outer.addWidget(self._content)

    def add_widget(self, widget: QWidget):
        self._content_layout.addWidget(widget)

    def add_layout(self, layout):
        self._content_layout.addLayout(layout)

    def _on_toggled(self, checked: bool):
        self._toggle_btn.setArrowType(Qt.UpArrow if checked else Qt.DownArrow)
        self._content.setVisible(checked)


# ─────────────────────────────────────────────────────────────────────────────
class SRTDialog(QDialog):
    """
    'Download SRT Subtitles' dialog.

    Lets the user configure smart re-segmentation parameters and immediately
    download the resulting .srt file.
    """

    def __init__(self, transcript: dict, parent=None):
        super().__init__(parent)
        self.transcript = transcript
        self.setWindowTitle("Download SRT Subtitles")
        self.setMinimumWidth(440)
        self.setObjectName("SRTDialog")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)

        # ── Max Words Per Segment ────────────────────────────────────────
        root.addLayout(_field_row(
            "Max Words Per Segment",
            "Each subtitle card will contain at most this many words.\n"
            "Lower values = shorter, easier-to-read cards."
        ))

        self.max_words_spin = QSpinBox()
        self.max_words_spin.setRange(1, 50)
        self.max_words_spin.setValue(8)
        self.max_words_spin.setFixedHeight(38)
        self.max_words_spin.setObjectName("SpinField")
        root.addWidget(self.max_words_spin)

        # ── Advanced collapsible section ─────────────────────────────────
        adv = CollapsibleSection("Advanced Segmentation Settings")

        # Max Duration
        adv.add_layout(_field_row(
            "Max Duration Per Segment (Seconds)",
            "A card will be split if its audio span exceeds this duration,\n"
            "even if the word limit has not been reached."
        ))
        self.max_dur_spin = QDoubleSpinBox()
        self.max_dur_spin.setRange(1.0, 60.0)
        self.max_dur_spin.setValue(10.0)
        self.max_dur_spin.setSingleStep(0.5)
        self.max_dur_spin.setFixedHeight(38)
        self.max_dur_spin.setObjectName("SpinField")
        adv.add_widget(self.max_dur_spin)

        # Max Characters
        adv.add_layout(_field_row(
            "Max Characters Per Segment",
            "A card will be split before exceeding this character count\n"
            "(including spaces). Useful for narrow video formats."
        ))
        self.max_chars_spin = QSpinBox()
        self.max_chars_spin.setRange(10, 500)
        self.max_chars_spin.setValue(80)
        self.max_chars_spin.setFixedHeight(38)
        self.max_chars_spin.setObjectName("SpinField")
        adv.add_widget(self.max_chars_spin)

        # Sentence-Aware
        self.sentence_check = QCheckBox("Sentence-Aware Segmentation")
        self.sentence_check.setChecked(True)
        self.sentence_check.setObjectName("SentenceCheck")

        sent_desc = QLabel(
            "If enabled, the start of a new sentence will always begin\n"
            "a new segment."
        )
        sent_desc.setObjectName("SentenceDesc")
        sent_desc.setWordWrap(True)

        sent_widget = QWidget()
        sent_layout = QHBoxLayout(sent_widget)
        sent_layout.setContentsMargins(0, 4, 0, 0)
        sent_layout.setSpacing(12)
        sent_layout.addWidget(self.sentence_check, alignment=Qt.AlignTop)
        sent_layout.addWidget(sent_desc, stretch=1)

        adv.add_widget(sent_widget)

        root.addWidget(adv)

        # ── Download button ──────────────────────────────────────────────
        root.addSpacing(6)
        dl_btn = QPushButton("DOWNLOAD SRT")
        dl_btn.setObjectName("SRTDownloadBtn")
        dl_btn.setFixedHeight(46)
        font = dl_btn.font()
        font.setBold(True)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        dl_btn.setFont(font)
        dl_btn.clicked.connect(self._download)
        root.addWidget(dl_btn)

    # ─────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────

    def _download(self):
        name     = self.transcript.get("name", "transcript")
        segments = self.transcript.get("segments", [])

        # Build re-segmented list
        new_segs = resegment_for_srt(
            segments,
            max_words=self.max_words_spin.value(),
            max_duration=self.max_dur_spin.value(),
            max_chars=self.max_chars_spin.value(),
            sentence_aware=self.sentence_check.isChecked(),
        )

        srt_text = export_srt(new_segs)

        start_dir    = os.path.join(os.path.expanduser("~"), "Documents")
        initial_path = os.path.join(start_dir, f"{name}.srt")

        path, _ = QFileDialog.getSaveFileName(
            self, "Save SRT File", initial_path, "SRT Files (*.srt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(srt_text)
            self.accept()
