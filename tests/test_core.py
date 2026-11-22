"""Core tests: LZMA encoder, buffer layout, full print job against a simulated printer.

Run:  python -m pytest -q   (or python tests/test_core.py)
"""

import lzma
import os
import random
import sys
import types

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from studio import protocol  # noqa: E402
from studio.lzma_raw import compress_alone  # noqa: E402


def _decode(block: bytes) -> bytes:
    return lzma.decompress(block, format=lzma.FORMAT_ALONE)


def test_lzma_roundtrip_and_header():
    rnd = random.Random(3)
    samples = [b"", b"\x00" * 4096, bytes(rnd.getrandbits(8) for _ in range(3000)),
               bytes(rnd.choice([0, 0, 0, 0xFF, 0x0F]) for _ in range(12000)), b"abc" * 999]
    for s in samples:
        z = compress_alone(s)
        assert z[0] == 0x5D
        assert int.from_bytes(z[1:5], "little") == 8192
        assert int.from_bytes(z[5:13], "little") == len(s)
        assert _decode(z) == s


def test_no_end_marker():
    # an end marker costs a few bytes; our stream must be shorter than liblzma's
    data = bytes(4096)
    assert len(compress_alone(data)) < len(protocol.compress_stdlib(data))


def _check_buffer(buf: bytes):
    assert len(buf) == protocol.PRINT_BUF_SIZE
    cols = int.from_bytes(buf[4:6], "little")
    data_end = cols * buf[6] + protocol.PRINT_BUF_HEADER
    chk = sum(buf[2:14]) + sum(buf[i * 256 - 1] for i in range(1, data_end // 256 + 1))
    assert int.from_bytes(buf[0:2], "little") == chk & 0xFFFF
    return cols


def _reassemble(blocks):
    raw = b"".join(_decode(b) for b in blocks)
    bufs = [raw[i:i + 4096] for i in range(0, len(raw), 4096)]
    rows = []
    for b in bufs:
        cols = _check_buffer(b)
        d = np.frombuffer(b[14:14 + cols * 48], dtype=np.uint8).reshape(cols, 48)
        rows.append(np.unpackbits(d, axis=1, bitorder="little").astype(bool)[:, ::-1])
    return bufs, np.vstack(rows)


def test_line_orientation_not_mirrored():
    # A dot at the design's left edge must land in the last bit of each wire line,
    # otherwise the printer mirrors the label (see pack_columns).
    img = np.zeros((1, protocol.HEAD_DOTS), dtype=bool)
    img[0, 0] = True
    line = protocol.pack_columns(img)
    assert len(line) == protocol.HEAD_BYTES
    assert line[-1] == 0x80 and not any(line[:-1])


def test_page_roundtrip_short_and_long():
    rnd = np.random.default_rng(1)
    for feed_mm, width_mm in ((30, 40), (80, 50), (200, 48)):
        img = rnd.random((feed_mm * 8, width_mm * 8)) < 0.1
        page = protocol.encode_page(img, 7)
        assert all(len(b) <= 4096 for b in page.blocks)
        bufs, got = _reassemble(page.blocks)
        exp = protocol.center_on_head(img)[protocol.MARGIN_DOTS:-protocol.MARGIN_DOTS]
        assert got.shape == exp.shape
        assert (got == exp).all()
        assert bufs[0][2] & 0x02 and bufs[-1][2] & 0x04 and bufs[-1][2] & 0x08
        assert ((bufs[0][3] >> 2) & 0x0F) == 7


class FakeHid:
    """Simulates the T50M Pro on the HID level."""

    def __init__(self):
        self.out = []
        self.pending = []
        self.bulk_left = 0
        self.bulk = bytearray()
        self.blocks = []
        self.printing = False
        self.polls_printing = 0

    # hidapi API ---------------------------------------------------------
    def open_path(self, p): pass
    def set_nonblocking(self, v): pass
    def close(self): pass

    def write(self, data):
        data = bytes(data[1:])
        assert len(data) == 64
        if self.bulk_left:
            take = min(64, self.bulk_left)
            self.bulk += data[:take]
            self.bulk_left -= take
            if not self.bulk_left:
                self.blocks.append(bytes(self.bulk))
                self.pending.append(b"\x01" + bytes(63))
            return 65
        assert data[:2] == b"\xC0\x40"
        cmd, p1 = data[4], (data[2] << 8) | data[3]
        if cmd == 0x11:
            b2 = 0x40 if self.printing else 0
            if self.printing and self.polls_printing > 3 and self.blocks_committed:
                self.printing = False
                b2 = 0
            self.polls_printing += 1
            self.pending.append(bytes([8, 0, 0, b2, 0, 5, 0]) + bytes(57))
        elif cmd == 0x30:
            r = bytearray(64)
            r[19], r[20], r[21] = 40, 30, 2
            r[40:47] = b"T50TEST"
            self.pending.append(bytes(r))
        elif cmd == 0x13:
            self.printing = True
            self.blocks_committed = False
            self.pending.append(bytes(64))
        elif cmd == 0x5C:
            self.bulk_left = p1
            self.bulk = bytearray()
            self.pending.append(bytes(64))
        elif cmd == 0x10:
            self.blocks_committed = True
            self.polls_printing = 0
            self.pending.append(bytes(64))
        else:
            self.pending.append(bytes(64))
        return 65

    def read(self, n, timeout):
        return list(self.pending.pop(0)) if self.pending else []


def test_full_job_against_fake_printer(monkeypatch=None):
    fake = FakeHid()
    mod = types.SimpleNamespace(device=lambda: fake,
                                enumerate=lambda vid: [{"path": b"x", "usage_page": 0xFF00, "product_id": 0x207F}])
    sys.modules["hid"] = mod
    from studio import printer as pr
    pr.time.sleep = lambda s: None
    p = pr.Printer.open()
    assert p.material().width_mm == 40 and p.material().serial == "T50TEST"
    rnd = np.random.default_rng(2)
    imgs = [rnd.random((240, 320)) < 0.05, rnd.random((640, 384)) < 0.3]
    p.print_images(imgs, density=9)
    got_imgs = []
    raw_bufs, cur = [], []
    for b in fake.blocks:
        raw = _decode(b)
        for i in range(0, len(raw), 4096):
            buf = raw[i:i + 4096]
            cur.append(buf)
            if buf[2] & 0x04:
                raw_bufs.append(cur)
                cur = []
    assert len(raw_bufs) == 2
    for page_bufs, img in zip(raw_bufs, imgs):
        rows = []
        for b in page_bufs:
            cols = _check_buffer(b)
            d = np.frombuffer(b[14:14 + cols * 48], dtype=np.uint8).reshape(cols, 48)
            rows.append(np.unpackbits(d, axis=1, bitorder="little").astype(bool)[:, ::-1])
        got = np.vstack(rows)
        exp = protocol.center_on_head(img)[8:-8]
        assert (got == exp).all()
        got_imgs.append(got)
    assert not (raw_bufs[0][-1][2] & 0x08) and raw_bufs[1][-1][2] & 0x08


def test_model_and_render():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from studio.model import Document, Element, substitute, builtin_vars
    from studio.render import head_image
    assert substitute("Nr {nr:4} {Preis} {x}", {"nr": 7, "preis": "3,50"}) == "Nr 0007 3,50 {x}"
    d = Document(width_mm=40, length_mm=30)
    d.elements = [Element.new("rect", x=0, y=0, w=40, h=30, props={"fill": True})]
    a = head_image(d, builtin_vars())
    assert a.shape == (240, 320) and a.mean() > 0.99
    d.landscape = True
    a = head_image(d, builtin_vars())
    assert a.shape == (240, 320)  # still feed x across after rotation
    d2 = Document.from_json(d.to_json())
    assert d2.elements[0].props["fill"] is True and d2.landscape


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
