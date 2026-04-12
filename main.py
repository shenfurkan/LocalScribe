import sys
import os

# Ensure the project root is always on sys.path regardless of the
# working directory the user launches the script from.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Change the working directory to the project root so that relative
# paths (e.g. "transcripts/", "assets/style.qss") always resolve correctly.
os.chdir(_ROOT)

# Suppress Hugging Face Hub symlinks warnings on Windows
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QIcon
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("LocalScribe")
    app.setOrganizationName("LocalScribe")

    # ── Default font (Windows-native) ─────────────────────────────────
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setWindowIcon(QIcon(os.path.join(_ROOT, "image", "LocalScribe.ico")))

    # ── Launch ─────────────────────────────────────────────────────────
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
