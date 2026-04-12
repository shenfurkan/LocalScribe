from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton,
    QCheckBox, QPushButton, QButtonGroup, QFileDialog, QMenu
)
from core.exporter import export_txt, export_srt, export_vtt, export_csv, export_docx, export_pdf
import os

FORMATS = [
    ("TXT",  "Plain text",             ".txt"),
    ("SRT",  "SubRip Subtitles",       ".srt"),
    ("VTT",  "WebVTT Subtitles",       ".vtt"),
    ("CSV",  "Spreadsheet (CSV)",      ".csv"),
    ("DOCX", "Word Document (.docx)",  ".docx"),
    ("PDF",  "PDF Document",           ".pdf"),
]

class ExportDialog(QDialog):
    def __init__(self, transcript: dict, parent=None):
        super().__init__(parent)
        self.transcript = transcript
        self.setWindowTitle("Export Transcript")
        self.setMinimumWidth(380)
        
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("Choose export format:"))
        
        self.btn_group = QButtonGroup(self)
        for key, label, ext in FORMATS:
            rb = QRadioButton(f"{key} — {label}")
            rb.setProperty("format_key", key)
            self.btn_group.addButton(rb)
            layout.addWidget(rb)
            if key == "TXT":
                rb.setChecked(True)
        
        self.ts_checkbox = QCheckBox("Include timestamps")
        self.ts_checkbox.setChecked(True)
        layout.addWidget(self.ts_checkbox)
        
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        export_btn = QPushButton("Export")
        export_btn.setObjectName("PrimaryBtn")
        export_btn.clicked.connect(self._do_export)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(export_btn)
        layout.addLayout(btn_row)

    def _do_export(self):
        checked = self.btn_group.checkedButton()
        if not checked:
            return
        
        fmt = checked.property("format_key")
        include_ts = self.ts_checkbox.isChecked()
        name = self.transcript["name"]
        segs = self.transcript["segments"]

        # SRT export uses the dedicated smart-segmentation dialog.
        if fmt == "SRT":
            from ui.dialogs.srt_dialog import SRTDialog
            dlg = SRTDialog(self.transcript, parent=self)
            if dlg.exec():
                self.accept()
            return
        
        ext_map = {k: e for k, _, e in FORMATS}
        # default path in Documents
        start_dir = os.path.join(os.path.expanduser("~"), "Documents")
        initial_path = os.path.join(start_dir, f"{name}{ext_map[fmt]}")

        path, _ = QFileDialog.getSaveFileName(
            self, f"Save as {fmt}", initial_path,
            f"{fmt} Files (*{ext_map[fmt]})"
        )
        if not path:
            return
        
        if fmt == "TXT":
            with open(path, "w", encoding="utf-8") as f:
                f.write(export_txt(segs, include_ts))
        elif fmt == "VTT":
            with open(path, "w", encoding="utf-8") as f:
                f.write(export_vtt(segs))
        elif fmt == "CSV":
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(export_csv(segs))
        elif fmt == "DOCX":
            export_docx(name, segs, include_ts, save_path=path)
        elif fmt == "PDF":
            export_pdf(name, segs, include_ts, save_path=path)
        
        self.accept()


def show_quick_download_menu(parent, transcript, button):
    menu = QMenu(parent)
    name = transcript["name"]
    segs = transcript["segments"]
    ext_map = {k: e for k, _, e in FORMATS}
    
    def dl_handler(fmt):
        start_dir = os.path.join(os.path.expanduser("~"), "Documents")
        initial_path = os.path.join(start_dir, f"{name}{ext_map[fmt]}")
        path, _ = QFileDialog.getSaveFileName(
            parent, f"Save as {fmt}", initial_path,
            f"{fmt} Files (*{ext_map[fmt]})"
        )
        if not path:
            return
            
        if fmt == "TXT":
            with open(path, "w", encoding="utf-8") as f:
                f.write(export_txt(segs, True))
        elif fmt == "SRT":
            with open(path, "w", encoding="utf-8") as f:
                f.write(export_srt(segs))
        elif fmt == "VTT":
            with open(path, "w", encoding="utf-8") as f:
                f.write(export_vtt(segs))
        elif fmt == "CSV":
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(export_csv(segs))
        elif fmt == "DOCX":
            export_docx(name, segs, True, save_path=path)
        elif fmt == "PDF":
            export_pdf(name, segs, True, save_path=path)

    for fmt, label, _ in FORMATS:
        action = menu.addAction(f"{fmt} — {label}")
        # Need to capture fmt
        action.triggered.connect(lambda checked=False, f=fmt: dl_handler(f))

    # Show menu right below the button
    menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
