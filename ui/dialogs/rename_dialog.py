from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton

class RenameDialog(QDialog):
    def __init__(self, current_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rename Transcript")
        self.setMinimumWidth(300)
        
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("New name:"))
        
        self.name_input = QLineEdit()
        self.name_input.setText(current_name)
        # Select all text except potential extension
        if "." in current_name:
            idx = current_name.rfind(".")
            self.name_input.setSelection(0, idx)
        else:
            self.name_input.selectAll()
            
        layout.addWidget(self.name_input)
        
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        save_btn = QPushButton("Save")
        save_btn.setObjectName("PrimaryBtn")
        save_btn.clicked.connect(self.accept)
        
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        
        layout.addLayout(btn_row)

    def get_name(self) -> str:
        new_name = self.name_input.text().strip()
        return new_name if new_name else "Untitled"
