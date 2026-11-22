"""Mutschiketten – Start."""

import sys

from PySide6.QtWidgets import QApplication

from studio.mainwindow import MainWindow
from studio.style import apply_style


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Mutschiketten")
    app.setOrganizationName("EtikettStudio")
    apply_style(app)
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".etikett"):
        w.open_path(sys.argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
