"""Drawing of label elements with QPainter (shared by editor and printing).

All coordinates are printer dots (8 dots per mm, 203 dpi).
"""

from __future__ import annotations

import base64
import hashlib
import io

import numpy as np
from PIL import Image, ImageEnhance, ImageOps
from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen

from .model import DPMM, Document, Element, substitute

BLACK = QColor(0, 0, 0)
WHITE = QColor(255, 255, 255)
PT_TO_DOTS = 203.0 / 72.0

_img_cache: dict[tuple, QImage] = {}
_src_cache: dict[str, Image.Image] = {}


# ----------------------------------------------------------------- images
def decode_image(data_b64: str) -> Image.Image | None:
    if not data_b64:
        return None
    key = hashlib.sha1(data_b64.encode()).hexdigest()
    if key not in _src_cache:
        try:
            img = Image.open(io.BytesIO(base64.b64decode(data_b64)))
            img.load()
        except Exception:
            return None
        if img.mode in ("RGBA", "LA", "P", "PA"):
            img = img.convert("RGBA")
            bg = Image.new("RGBA", img.size, "white")
            img = Image.alpha_composite(bg, img)
        _src_cache[key] = img.convert("L")
        if len(_src_cache) > 64:
            _src_cache.pop(next(iter(_src_cache)))
    return _src_cache[key]


def encode_image_file(path: str, max_side: int = 1600) -> str:
    img = Image.open(path)
    img.load()
    return encode_pil(img, max_side)


def encode_pil(img: Image.Image, max_side: int = 1600) -> str:
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    if img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def pil_to_qimage(img: Image.Image) -> QImage:
    img = img.convert("L")
    data = img.tobytes()
    q = QImage(data, img.width, img.height, img.width, QImage.Format_Grayscale8)
    return q.copy()


def dithered_image(el: Element, w: int, h: int) -> tuple[QImage | None, QRectF]:
    """1-bit version of an image element, prepared at exact dot size."""
    src = decode_image(el.p("data"))
    if src is None or w < 1 or h < 1:
        return None, QRectF()
    key = (hashlib.sha1(el.p("data").encode()).hexdigest(), w, h, el.p("keep_aspect"), el.p("dither"),
           el.p("threshold"), el.p("invert"), el.p("contrast"), el.p("brightness"))
    if key in _img_cache:
        q = _img_cache[key]
    else:
        img = src
        if el.p("keep_aspect"):
            s = min(w / img.width, h / img.height)
            tw, th = max(1, round(img.width * s)), max(1, round(img.height * s))
        else:
            tw, th = w, h
        img = img.resize((tw, th), Image.LANCZOS)
        if el.p("brightness") != 1.0:
            img = ImageEnhance.Brightness(img).enhance(float(el.p("brightness")))
        if el.p("contrast") != 1.0:
            img = ImageEnhance.Contrast(img).enhance(float(el.p("contrast")))
        if el.p("invert"):
            img = ImageOps.invert(img)
        if el.p("dither") == "floyd":
            img = img.convert("1", dither=Image.FLOYDSTEINBERG)
        else:
            t = int(el.p("threshold"))
            img = img.point(lambda v: 255 if v >= t else 0).convert("1", dither=Image.NONE)
        q = pil_to_qimage(img)
        _img_cache[key] = q
        if len(_img_cache) > 128:
            _img_cache.pop(next(iter(_img_cache)))
    x = (w - q.width()) // 2
    y = (h - q.height()) // 2
    return q, QRectF(x, y, q.width(), q.height())


# ----------------------------------------------------------------- text
def _font(el: Element, px: float) -> QFont:
    f = QFont(el.p("font"))
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(bool(el.p("bold")))
    f.setItalic(bool(el.p("italic")))
    f.setUnderline(bool(el.p("underline")))
    f.setHintingPreference(QFont.PreferFullHinting)
    f.setStyleStrategy(QFont.PreferAntialias)
    return f


def _text_size(text: str, f: QFont, spacing: float) -> QSizeF:
    fm = QFontMetricsF(f)
    lines = text.split("\n") or [""]
    w = max((fm.horizontalAdvance(l) for l in lines), default=0.0)
    h = fm.height() * (1 + (len(lines) - 1) * spacing)
    return QSizeF(w, h)


def _draw_text(p: QPainter, el: Element, r: QRectF, values: dict | None) -> None:
    text = substitute(str(el.p("text")), values)
    spacing = float(el.p("line_spacing") or 1.0)
    px = float(el.p("size_pt")) * PT_TO_DOTS
    if el.p("autofit"):
        # largest size (up to the chosen one) that fits the box
        lo, hi = 2.0, max(px, 2.0)
        if _fits(text, _font(el, hi), spacing, r):
            px = hi
        else:
            for _ in range(14):
                mid = (lo + hi) / 2
                if _fits(text, _font(el, mid), spacing, r):
                    lo = mid
                else:
                    hi = mid
            px = lo
    f = _font(el, px)
    fg = WHITE if el.p("invert") else BLACK
    if el.p("invert"):
        p.fillRect(r, BLACK)
    p.setFont(f)
    p.setPen(fg)
    fm = QFontMetricsF(f)
    lines = text.split("\n")
    line_h = fm.height() * spacing
    total = fm.height() + line_h * (len(lines) - 1)
    va = el.p("valign")
    y0 = r.top() if va == "top" else (r.bottom() - total if va == "bottom" else r.top() + (r.height() - total) / 2)
    ha = el.p("halign")
    for i, line in enumerate(lines):
        lw = fm.horizontalAdvance(line)
        x = r.left() if ha == "left" else (r.right() - lw if ha == "right" else r.left() + (r.width() - lw) / 2)
        p.drawText(QPointF(x, y0 + i * line_h + fm.ascent()), line)


def _fits(text: str, f: QFont, spacing: float, r: QRectF) -> bool:
    s = _text_size(text, f, spacing)
    return s.width() <= r.width() + 0.5 and s.height() <= r.height() + 0.5


# ----------------------------------------------------------------- codes
def _draw_qr(p: QPainter, el: Element, r: QRectF, values: dict | None) -> None:
    import segno
    data = substitute(str(el.p("data")), values) or " "
    try:
        qr = segno.make(data, error=str(el.p("ec")).lower(), micro=False)
    except Exception:  # noqa: BLE001
        _draw_error(p, r, "QR?")
        return
    m = [list(row) for row in qr.matrix]
    n = len(m)
    mod = int(min(r.width(), r.height()) // n)
    if mod < 1:
        _draw_error(p, r, "QR zu klein")
        return
    size = mod * n
    ox = round(r.left() + (r.width() - size) / 2)
    oy = round(r.top() + (r.height() - size) / 2)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, False)
    for yy, row in enumerate(m):
        for xx, v in enumerate(row):
            if v & 1:
                p.fillRect(ox + xx * mod, oy + yy * mod, mod, mod, BLACK)
    p.restore()


BARCODES = {
    "code128": "Code 128", "ean13": "EAN-13", "ean8": "EAN-8", "code39": "Code 39",
    "upca": "UPC-A", "itf": "ITF", "isbn13": "ISBN-13",
}


def barcode_modules(symbology: str, data: str) -> tuple[str, str]:
    import barcode
    cls = barcode.get_barcode_class(symbology)
    kw = {"add_checksum": False} if symbology == "code39" else {}
    bc = cls(data, writer=None, **kw)
    mods = "".join(bc.build())
    return mods, bc.get_fullcode()


def _draw_barcode(p: QPainter, el: Element, r: QRectF, values: dict | None) -> None:
    data = substitute(str(el.p("data")), values)
    try:
        mods, full = barcode_modules(str(el.p("symbology")), data)
    except Exception:  # noqa: BLE001
        _draw_error(p, r, "Barcode-Daten ungültig")
        return
    n = len(mods)
    mod = int(r.width() // n)
    if mod < 1:
        _draw_error(p, r, "Barcode zu schmal")
        return
    text_h = 0.0
    f = None
    if el.p("show_text"):
        f = QFont("Arial")
        f.setPixelSize(max(6, int(float(el.p("font_pt")) * PT_TO_DOTS)))
        text_h = QFontMetricsF(f).height()
    bar_h = max(1.0, r.height() - text_h)
    ox = round(r.left() + (r.width() - mod * n) / 2)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, False)
    i = 0
    while i < n:
        if mods[i] == "1":
            j = i
            while j < n and mods[j] == "1":
                j += 1
            p.fillRect(QRectF(ox + i * mod, r.top(), (j - i) * mod, bar_h), BLACK)
            i = j
        else:
            i += 1
    p.restore()
    if f is not None:
        p.setFont(f)
        p.setPen(BLACK)
        p.drawText(QRectF(r.left(), r.top() + bar_h, r.width(), text_h), Qt.AlignCenter, full)


def _draw_error(p: QPainter, r: QRectF, msg: str) -> None:
    p.save()
    pen = QPen(BLACK, 1, Qt.DashLine)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
    f = QFont("Arial")
    f.setPixelSize(max(8, int(min(r.height() / 3, 24))))
    p.setFont(f)
    p.drawText(r, Qt.AlignCenter | Qt.TextWordWrap, msg)
    p.restore()


# ----------------------------------------------------------------- shapes
def _draw_shape(p: QPainter, el: Element, r: QRectF) -> None:
    lw = max(1.0, float(el.p("line_mm")) * DPMM)
    p.save()
    if el.type == "line":
        p.setPen(Qt.NoPen)
        if r.width() >= r.height():
            p.fillRect(QRectF(r.left(), r.center().y() - lw / 2, r.width(), lw), BLACK)
        else:
            p.fillRect(QRectF(r.center().x() - lw / 2, r.top(), lw, r.height()), BLACK)
        p.restore()
        return
    fill = bool(el.p("fill"))
    if fill:
        p.setPen(Qt.NoPen)
        p.setBrush(BLACK)
        inner = r
    else:
        pen = QPen(BLACK, lw)
        pen.setJoinStyle(Qt.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        inner = r.adjusted(lw / 2, lw / 2, -lw / 2, -lw / 2)
    if el.type == "ellipse":
        p.drawEllipse(inner)
    else:
        rad = float(el.p("radius_mm")) * DPMM
        if rad > 0:
            p.drawRoundedRect(inner, rad, rad)
        else:
            p.drawRect(inner)
    p.restore()


# ----------------------------------------------------------------- public
def draw_element(p: QPainter, el: Element, values: dict | None = None) -> None:
    """Draw *el* into the local rect (0, 0, w, h) in dots."""
    r = QRectF(0, 0, el.w * DPMM, el.h * DPMM)
    t = el.type
    if t == "text":
        _draw_text(p, el, r, values)
    elif t == "image":
        q, target = dithered_image(el, int(round(r.width())), int(round(r.height())))
        if q is None:
            _draw_error(p, r, "Bild wählen …")
        else:
            p.save()
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(target, q)
            p.restore()
    elif t == "qr":
        _draw_qr(p, el, r, values)
    elif t == "barcode":
        _draw_barcode(p, el, r, values)
    else:
        _draw_shape(p, el, r)


def render_design(doc: Document, values: dict | None = None, offset_mm: tuple[float, float] = (0, 0)) -> QImage:
    """Render the design canvas as an 8-bit grayscale image (white background)."""
    W = int(round(doc.design_w * DPMM))
    H = int(round(doc.design_h * DPMM))
    img = QImage(W, H, QImage.Format_Grayscale8)
    img.fill(255)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.translate(offset_mm[0] * DPMM, offset_mm[1] * DPMM)
    for el in doc.elements:
        p.save()
        w, h = el.w * DPMM, el.h * DPMM
        p.translate(el.x * DPMM, el.y * DPMM)
        if el.rotation:
            p.translate(w / 2, h / 2)
            p.rotate(el.rotation)
            p.translate(-w / 2, -h / 2)
        draw_element(p, el, values)
        p.restore()
    p.end()
    return img


def qimage_to_bool(img: QImage, threshold: int = 128) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_Grayscale8)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl)[:, :w]
    return arr < threshold


def bool_to_qimage(arr: np.ndarray) -> QImage:
    g = np.where(arr, 0, 255).astype(np.uint8)
    h, w = g.shape
    q = QImage(g.tobytes(), w, h, w, QImage.Format_Grayscale8)
    return q.copy()


def head_image(doc: Document, values: dict | None = None, *, rotate180: bool = False,
               landscape_cw: bool = True, offset_mm: tuple[float, float] = (0, 0),
               threshold: int = 128) -> np.ndarray:
    """Design -> head orientation: rows = feed, columns = across the head."""
    arr = qimage_to_bool(render_design(doc, values, offset_mm), threshold)
    if doc.landscape:
        arr = np.rot90(arr, k=-1 if landscape_cw else 1)
    if rotate180:
        arr = np.rot90(arr, 2)
    return np.ascontiguousarray(arr)
