"""USB-HID connection and print job for the Katasymbol / SUPVAN T50M Pro."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import protocol

log = logging.getLogger("studio.printer")

VID = 0x1820
REPORT = 64

CMD_BUF_FULL = 0x10
CMD_INQUIRY_STA = 0x11
CMD_CHECK_DEVICE = 0x12
CMD_START_PRINT = 0x13
CMD_STOP_PRINT = 0x14
CMD_RETURN_MAT = 0x30
CMD_NEXT_ZIPPEDBULK = 0x5C


class PrinterError(RuntimeError):
    pass


@dataclass
class Status:
    buf_full: bool = False
    label_rw_error: bool = False
    label_end: bool = False
    label_mode_error: bool = False
    ribbon_rw_error: bool = False
    ribbon_end: bool = False
    low_battery: bool = False
    busy: bool = False
    head_hot: bool = False
    cover_open: bool = False
    usb: bool = False
    printing: bool = False
    no_label: bool = False
    count: int = 0

    def errors(self) -> list[str]:
        e = []
        if self.cover_open:
            e.append("Deckel offen")
        if self.no_label:
            e.append("Keine Etikettenrolle eingelegt")
        if self.label_end:
            e.append("Etiketten aufgebraucht")
        if self.label_rw_error:
            e.append("Etikettenrolle nicht erkannt")
        if self.label_mode_error:
            e.append("Falscher Etikettentyp")
        if self.ribbon_end or self.ribbon_rw_error:
            e.append("Farbband-Fehler")
        if self.head_hot:
            e.append("Druckkopf zu heiß")
        return e

    def warnings(self) -> list[str]:
        return ["Akku schwach"] if self.low_battery else []


@dataclass
class Material:
    width_mm: int = 0   # across the print head
    length_mm: int = 0  # along the feed, 0 = endlos
    gap_mm: int = 0
    label_type: int = 0
    serial: str = ""


def parse_status(r: bytes) -> Status:
    if len(r) < 7:
        raise PrinterError("Statusantwort zu kurz")
    b0, b1, b2, b3 = r[1], r[2], r[3], r[4]
    return Status(
        buf_full=bool(b0 & 0x01), label_rw_error=bool(b0 & 0x02), label_end=bool(b0 & 0x04),
        label_mode_error=bool(b0 & 0x08), ribbon_rw_error=bool(b0 & 0x10), ribbon_end=bool(b0 & 0x20),
        low_battery=bool(b0 & 0x40), busy=bool(b1 & 0x04), head_hot=bool(b1 & 0x08),
        cover_open=bool(b2 & 0x08), usb=bool(b2 & 0x10), printing=bool(b2 & 0x40),
        no_label=bool(b3 & 0x01), count=r[5] | (r[6] << 8),
    )


def parse_material(r: bytes) -> Material:
    if len(r) < 23:
        raise PrinterError("Material-Antwort zu kurz")
    serial = ""
    if len(r) > 40:
        serial = r[40:].split(b"\0", 1)[0].decode("ascii", "replace")
    return Material(width_mm=r[19], length_mm=r[20], gap_mm=r[21], label_type=r[22], serial=serial)


def make_cmd(cmd: int, p1: int = 0, p2: int | None = None) -> bytes:
    f = bytearray([0xC0, 0x40, (p1 >> 8) & 0xFF, p1 & 0xFF, cmd, 0x00, 0x08, 0x00])
    if p2 is not None:
        f += bytes([(p2 >> 8) & 0xFF, p2 & 0xFF])
    return bytes(f)


def find_devices() -> list[dict]:
    import hid  # hidapi
    devs = [d for d in hid.enumerate(VID) if d.get("usage_page", 0) >= 0xFF00]
    if not devs:  # some Windows builds report usage_page 0 for every interface
        devs = list(hid.enumerate(VID))
    return devs


class Printer:
    """One open HID connection. All I/O is serialised by a lock."""

    def __init__(self, path: bytes):
        import hid
        self._dev = hid.device()
        self._dev.open_path(path)
        self._dev.set_nonblocking(0)
        self._io = threading.RLock()

    @classmethod
    def open(cls) -> "Printer":
        devs = find_devices()
        if not devs:
            raise PrinterError("Kein T50M Pro gefunden. Drucker einschalten und per USB anschließen.")
        return cls(devs[0]["path"])

    def close(self) -> None:
        try:
            self._dev.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # --------------------------------------------------------------- low level
    def _write(self, data: bytes) -> None:
        if self._dev.write(b"\x00" + data[:REPORT].ljust(REPORT, b"\0")) < 0:
            raise PrinterError("Schreiben zum Drucker fehlgeschlagen")

    def _read(self, timeout_ms: int = 2000) -> bytes:
        r = self._dev.read(REPORT, timeout_ms)
        return bytes(r) if r else b""

    def cmd(self, cmd: int, p1: int = 0, p2: int | None = None, timeout_ms: int = 2000) -> bytes:
        with self._io:
            self._write(make_cmd(cmd, p1, p2))
            return self._read(timeout_ms)

    # --------------------------------------------------------------- queries
    def status(self) -> Status:
        r = self.cmd(CMD_INQUIRY_STA)
        if not r:
            raise PrinterError("Drucker antwortet nicht")
        return parse_status(r)

    def material(self) -> Material:
        r = self.cmd(CMD_RETURN_MAT)
        if not r:
            raise PrinterError("Drucker antwortet nicht")
        return parse_material(r)

    def _poll(self, what: str, timeout_s: float, interval: float, ok: Callable[[Status], bool]) -> Status:
        end = time.monotonic() + timeout_s
        while True:
            st = self.status()
            if st.errors():
                raise PrinterError(", ".join(st.errors()))
            if ok(st):
                return st
            if time.monotonic() > end:
                raise PrinterError(f"Zeitüberschreitung beim Warten auf {what}")
            time.sleep(interval)

    # --------------------------------------------------------------- printing
    def _send_page(self, page: protocol.Page) -> None:
        for block in page.blocks:
            self._poll("freien Druckerpuffer", 15, 0.02, lambda s: not s.buf_full)
            if not self.cmd(CMD_NEXT_ZIPPEDBULK, len(block)):
                raise PrinterError("Keine Antwort auf Datenübertragung")
            for i in range(0, len(block), REPORT):
                self._write(block[i:i + REPORT])
            self._read(3000)  # device acks once all announced bytes arrived
            time.sleep(0.15)
            if not self.cmd(CMD_BUF_FULL, len(block), page.speed):
                raise PrinterError("Keine Antwort auf Druckbefehl")
            time.sleep(0.1)

    def print_images(self, images: list[np.ndarray], density: int = 8, method: str = "native",
                     progress: Callable[[str], None] | None = None,
                     cancel: threading.Event | None = None) -> None:
        """Print a list of head images (one per label) as one job."""
        say = progress or (lambda m: None)
        pages = []
        for i, img in enumerate(images):
            say(f"Kodiere Etikett {i + 1}/{len(images)} …")
            pages.append(protocol.encode_page(img, density, last_page=(i == len(images) - 1), method=method))
        with self._io:
            if not self.cmd(CMD_CHECK_DEVICE):
                raise PrinterError("Drucker reagiert nicht. Ist er eingeschaltet?")
            self._poll("Bereitschaft", 6, 0.1, lambda s: not s.busy and not s.printing)
            self.cmd(CMD_START_PRINT, 1)
            try:
                self._poll("Druckstart", 6, 0.1, lambda s: s.printing)
                for i, page in enumerate(pages):
                    if cancel is not None and cancel.is_set():
                        raise PrinterError("Abgebrochen")
                    say(f"Drucke Etikett {i + 1}/{len(pages)} …")
                    self._send_page(page)
                self._poll("Druckende", 60, 0.1, lambda s: not s.printing and not s.busy)
                say("Fertig")
            except BaseException:
                try:
                    self.cmd(CMD_STOP_PRINT)
                except Exception:
                    pass
                raise
