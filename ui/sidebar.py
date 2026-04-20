from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QScrollArea, QFrame
)
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt
import os
from ui.widgets.transcript_card import TranscriptCard
from core.storage import StorageManager

class Sidebar(QWidget):
    file_selected = Signal(str)        # transcript_id
    upload_requested = Signal()
    theme_toggled = Signal()
    gpu_settings_requested = Signal()

    def __init__(self, storage: StorageManager):
        super().__init__()
        self.storage = storage
        self.setObjectName("Sidebar")
        self.setFixedWidth(252)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Branding header
        self.header = QLabel()
        self.header.setObjectName("SidebarHeader")
        self.header.setAlignment(Qt.AlignCenter)
        self.header.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(self.header)
        
        # Upload button
        upload_btn = QPushButton("＋  New Transcription")
        upload_btn.setObjectName("UploadBtn")
        upload_btn.setCursor(Qt.PointingHandCursor)
        upload_btn.clicked.connect(self.upload_requested)
        layout.addWidget(upload_btn)

        self.section_label = QLabel("Recent Transcripts")
        self.section_label.setObjectName("SidebarSectionLabel")
        layout.addWidget(self.section_label)
        
        # Scrollable file list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("SidebarScroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(10, 8, 10, 10)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch()
        
        scroll.setWidget(self.list_container)
        layout.addWidget(scroll, stretch=1)
        
        # Status Label
        self.status_label = QLabel("")
        self.status_label.setObjectName("SidebarStatus")
        self.status_label.setWordWrap(True)

        # Theme toggle button at bottom
        self.theme_btn = QPushButton("🌗  Appearance")
        self.theme_btn.setObjectName("SidebarThemeBtn")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.clicked.connect(self.theme_toggled.emit)
        
        # GPU settings button
        self.gpu_btn = QPushButton("🖥  GPU Acceleration")
        self.gpu_btn.setObjectName("SidebarThemeBtn")
        self.gpu_btn.setCursor(Qt.PointingHandCursor)
        self.gpu_btn.clicked.connect(self.gpu_settings_requested.emit)

        # Add to layout with some margin
        bottom_layout = QVBoxLayout()
        bottom_layout.setContentsMargins(12, 12, 12, 12)
        bottom_layout.addWidget(self.status_label)
        bottom_layout.addWidget(self.gpu_btn)
        bottom_layout.addWidget(self.theme_btn)
        layout.addLayout(bottom_layout)
        
        self.refresh_file_list()

    def set_status(self, msg: str):
        self.status_label.setText(msg)

    def update_theme(self, theme: str, base_dir: str):
        filename = "localscribeheaderdark.png" if theme == "dark" else "localscribeheaderlight.png"
        path = os.path.join(base_dir, "image", filename)
        img = QImage(path)
        if not img.isNull():
            # Handle High-DPI scaling: Scale to a higher physical resolution
            # but set the logical devicePixelRatio so it remains sharp.
            dpr = self.devicePixelRatioF()
            # If dpr is 1.0, artificially doubling it and letting the label scale it down 
            # often yields much sharper results on Windows for text-heavy logos.
            render_ratio = max(dpr, 2.0) 
            
            logical_width = 110
            physical_width = int(logical_width * render_ratio)
            
            # QImage SmoothTransformation uses a much higher quality filter than QPixmap
            scaled_img = img.scaledToWidth(physical_width, Qt.SmoothTransformation)
            scaled_pix = QPixmap.fromImage(scaled_img)
            
            # Tell Qt this pixmap has a higher pixel density
            scaled_pix.setDevicePixelRatio(render_ratio)
            
            self.header.setPixmap(scaled_pix)
    
    def refresh_file_list(self):
        # Clear existing cards
        while self.list_layout.count() > 1:  # keep the stretch at the end
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        transcripts = self.storage.load_all()
        if not transcripts:
            empty = QLabel("No transcripts yet.\nClick New Transcription to get started.")
            empty.setObjectName("SidebarEmptyState")
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            self.list_layout.insertWidget(self.list_layout.count() - 1, empty)
            return

        for t in transcripts:
            card = TranscriptCard(t)
            card.clicked.connect(lambda _id=t["id"]: self.file_selected.emit(_id))
            self.list_layout.insertWidget(
                self.list_layout.count() - 1, card
            )
