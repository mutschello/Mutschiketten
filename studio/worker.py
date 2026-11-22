"""Background thread that owns the USB connection (status polling + printing)."""

from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from .printer import Printer, PrinterError


class PrinterWorker(QObject):
    status = Signal(object, object)      # Status | None, Material | None
    message = Signal(str)                # connection / error text for the status bar
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self._printer: Printer | None = None
        self._timer: QTimer | None = None
        self._busy = False
        self.cancel = threading.Event()
        self._last_mat_key = None

    @Slot()
    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(2500)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        QTimer.singleShot(200, self.poll)

    def _ensure(self) -> Printer:
        if self._printer is None:
            self._printer = Printer.open()
        return self._printer

    def _drop(self) -> None:
        if self._printer is not None:
            self._printer.close()
        self._printer = None

    @Slot()
    def poll(self) -> None:
        if self._busy:
            return
        try:
            p = self._ensure()
            st = p.status()
            mat = p.material()
            self.status.emit(st, mat)
        except ImportError:
            self.message.emit("hidapi fehlt – bitte 'pip install hidapi'")
            if self._timer:
                self._timer.stop()
        except Exception as e:  # noqa: BLE001
            self._drop()
            self.status.emit(None, None)
            self.message.emit(str(e) if isinstance(e, PrinterError) else f"Kein Drucker ({e})")

    @Slot(object, int, str)
    def print_job(self, images, density: int, method: str) -> None:
        self._busy = True
        self.cancel.clear()
        try:
            p = self._ensure()
            p.print_images(images, density, method, progress=self.progress.emit, cancel=self.cancel)
            self.finished.emit(True, f"{len(images)} Etikett(en) gedruckt")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._drop()
            self.finished.emit(False, str(e))
        finally:
            self._busy = False
            QTimer.singleShot(300, self.poll)

    @Slot()
    def stop(self) -> None:
        if self._timer:
            self._timer.stop()
        self._drop()


def start_worker(parent) -> tuple[PrinterWorker, QThread]:
    th = QThread(parent)
    w = PrinterWorker()
    w.moveToThread(th)
    th.started.connect(w.start)
    th.start()
    return w, th
