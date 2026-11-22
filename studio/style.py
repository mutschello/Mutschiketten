"""Look & feel: calm light Fusion theme with one accent colour."""

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#2563eb"

QSS = f"""
QMainWindow, QDockWidget, QTabWidget::pane {{ background: #f7f7f9; }}
QToolBar {{ background: #ffffff; border: none; border-bottom: 1px solid #e3e5ea; padding: 4px 6px; spacing: 2px; }}
QToolBar QToolButton {{ padding: 5px 9px; border-radius: 6px; color: #1f2937; }}
QToolBar QToolButton:hover {{ background: #eef1f6; }}
QToolBar QToolButton:checked {{ background: #dbe7ff; color: {ACCENT}; }}
QTabWidget::pane {{ border: none; border-left: 1px solid #e3e5ea; }}
QTabBar::tab {{ padding: 8px 16px; background: transparent; border: none; color: #6b7280; }}
QTabBar::tab:selected {{ color: #111827; border-bottom: 2px solid {ACCENT}; }}
QGroupBox {{ font-weight: 600; border: 1px solid #e3e5ea; border-radius: 8px; margin-top: 14px;
            padding: 10px 8px 8px 8px; background: #ffffff; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #374151; }}
QPushButton {{ padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 6px; background: #ffffff; }}
QPushButton:hover {{ background: #f3f4f6; }}
QPushButton#printButton {{ background: {ACCENT}; color: white; border: none; font-weight: 600; padding: 7px 18px; }}
QPushButton#printButton:hover {{ background: #1d4ed8; }}
QPushButton#printButton:disabled {{ background: #93a8d8; }}
QStatusBar {{ background: #ffffff; border-top: 1px solid #e3e5ea; }}
QLineEdit, QPlainTextEdit {{ border: 1px solid #d1d5db; border-radius: 5px; padding: 3px 5px; background: #ffffff; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
"""


def apply_style(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#f7f7f9"))
    pal.setColor(QPalette.Base, QColor("#ffffff"))
    pal.setColor(QPalette.AlternateBase, QColor("#f3f4f6"))
    pal.setColor(QPalette.Text, QColor("#111827"))
    pal.setColor(QPalette.WindowText, QColor("#111827"))
    pal.setColor(QPalette.ButtonText, QColor("#111827"))
    pal.setColor(QPalette.Button, QColor("#ffffff"))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Mid, QColor("#6b7280"))
    pal.setColor(QPalette.PlaceholderText, QColor("#9ca3af"))
    app.setPalette(pal)
    f = QFont("Segoe UI", 9)
    app.setFont(f)
    app.setStyleSheet(QSS)
