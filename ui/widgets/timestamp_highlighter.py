from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from PySide6.QtCore import QRegularExpression

class TimestampHighlighter(QSyntaxHighlighter):
    """
    Colors [HH:MM:SS] timestamps in the QTextEdit with a soft violet color.
    Applied to the QTextDocument — does not affect the underlying text.
    """
    def __init__(self, document):
        super().__init__(document)
        
        self.ts_format = QTextCharFormat()
        self.ts_format.setForeground(QColor("#96B6C5"))   # pastel blue accent
        self.ts_format.setFontItalic(True)
        self.ts_format.setFontPointSize(9)
        
        self.pattern = QRegularExpression(r"\[\d{2}:\d{2}:\d{2}\]")

    def highlightBlock(self, text: str):
        it = self.pattern.globalMatch(text)
        while it.hasNext():
            match = it.next()
            self.setFormat(
                match.capturedStart(),
                match.capturedLength(),
                self.ts_format
            )
