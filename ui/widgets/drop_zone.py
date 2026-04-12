from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from PySide6.QtGui import QDragEnterEvent, QDropEvent

SUPPORTED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".ogg", ".flac",
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".aac"
}

class DropZone(QWidget):
    files_dropped = Signal(list)   # list of file paths (strings)

    def __init__(self):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(200)
        
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        
        self.icon_label = QLabel()
        self.icon_label.setObjectName("DropZoneIcon")
        self.icon_label.setAlignment(Qt.AlignCenter)
        
        self.text_label = QLabel("Drag & drop audio or video files here")
        self.text_label.setObjectName("DropZoneText")
        self.text_label.setAlignment(Qt.AlignCenter)
        
        self.sub_label = QLabel(
            "Supported: MP3, WAV, M4A, OGG, FLAC, MP4, MKV, AVI, MOV, WebM"
        )
        self.sub_label.setObjectName("DropZoneSubText")
        self.sub_label.setAlignment(Qt.AlignCenter)
        
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        layout.addWidget(self.sub_label)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            valid = any(
                url.toLocalFile().lower().endswith(tuple(SUPPORTED_EXTENSIONS))
                for url in event.mimeData().urls()
            )
            if valid:
                event.acceptProposedAction()
                self.setProperty("dragging", True)
                self.style().polish(self)  # Re-apply QSS for drag state
    
    def dragLeaveEvent(self, event):
        self.setProperty("dragging", False)
        self.style().polish(self)

    def dropEvent(self, event: QDropEvent):
        self.setProperty("dragging", False)
        self.style().polish(self)
        paths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.toLocalFile().lower().endswith(tuple(SUPPORTED_EXTENSIONS))
        ]
        if paths:
            self.files_dropped.emit(paths)
            
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            from PySide6.QtWidgets import QFileDialog
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Select Audio / Video Files", "",
                "Media Files (*.mp3 *.wav *.m4a *.ogg *.flac *.mp4 *.mkv *.avi *.mov *.webm *.aac);;All Files (*.*)"
            )
            if paths:
                self.files_dropped.emit(paths)
