"""Mutschiketten – Start."""

import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

import studio
from studio.mainwindow import MainWindow
from studio.style import apply_style

ICON = os.path.join(os.path.dirname(studio.__file__), "mutschiketten.ico")


def main() -> int:
    if sys.platform == "win32":
        # Eigene App-ID, damit die Taskleiste unser Symbol zeigt und nicht das
        # von Python (wichtig beim Start über start.bat / pythonw.exe).
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mutschweb.Mutschiketten")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Mutschiketten")
    app.setOrganizationName("EtikettStudio")
    if os.path.exists(ICON):
        app.setWindowIcon(QIcon(ICON))
    apply_style(app)
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".etikett"):
        w.open_path(sys.argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
