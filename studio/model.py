"""Label document model (pure Python, JSON-serialisable)."""

from __future__ import annotations

import copy
import datetime as _dt
import json
import re
import uuid
from dataclasses import asdict, dataclass, field

DPMM = 8  # dots per mm

ELEMENT_DEFAULTS: dict[str, dict] = {
    "text": {
        "text": "Text", "font": "Arial", "size_pt": 14.0, "bold": False, "italic": False,
        "underline": False, "halign": "center", "valign": "center", "autofit": True,
        "invert": False, "line_spacing": 1.0,
    },
    "image": {"data": "", "keep_aspect": True, "dither": "floyd", "threshold": 128,
              "invert": False, "contrast": 1.0, "brightness": 1.0},
    "qr": {"data": "https://", "ec": "M"},
    "barcode": {"data": "123456789012", "symbology": "code128", "show_text": True, "font_pt": 7.0},
    "rect": {"line_mm": 0.5, "fill": False, "radius_mm": 0.0},
    "ellipse": {"line_mm": 0.5, "fill": False},
    "line": {"line_mm": 0.5},
}

TYPE_NAMES = {
    "text": "Text", "image": "Bild", "qr": "QR-Code", "barcode": "Barcode",
    "rect": "Rechteck", "ellipse": "Ellipse", "line": "Linie",
}


def snap_mm(v: float) -> float:
    """Round to whole printer dots (1/8 mm)."""
    return round(float(v) * DPMM) / DPMM


@dataclass
class Element:
    type: str
    x: float = 2.0
    y: float = 2.0
    w: float = 20.0
    h: float = 8.0
    rotation: float = 0.0
    props: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    locked: bool = False

    @classmethod
    def new(cls, type_: str, **kw) -> "Element":
        props = copy.deepcopy(ELEMENT_DEFAULTS[type_])
        props.update(kw.pop("props", {}))
        return cls(type=type_, props=props, **kw)

    def p(self, key: str):
        return self.props.get(key, ELEMENT_DEFAULTS[self.type].get(key))

    def clone(self) -> "Element":
        e = copy.deepcopy(self)
        e.id = uuid.uuid4().hex[:8]
        return e


@dataclass
class Document:
    width_mm: float = 40.0    # across the print head
    length_mm: float = 30.0   # along the feed
    landscape: bool = False   # design rotated by 90° relative to the feed
    elements: list[Element] = field(default_factory=list)
    density: int = 8
    copies: int = 1
    counter_start: int = 1
    counter_step: int = 1
    name: str = ""

    # design canvas size (what the user sees)
    @property
    def design_w(self) -> float:
        return self.length_mm if self.landscape else self.width_mm

    @property
    def design_h(self) -> float:
        return self.width_mm if self.landscape else self.length_mm

    def to_json(self) -> str:
        d = asdict(self)
        d["format"] = "etikett-studio/1"
        return json.dumps(d, ensure_ascii=False, indent=1)

    @classmethod
    def from_json(cls, s: str) -> "Document":
        d = json.loads(s)
        d.pop("format", None)
        els = [Element(**{k: v for k, v in e.items() if k in Element.__dataclass_fields__})
               for e in d.pop("elements", [])]
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        doc = cls(**known)
        doc.elements = els
        return doc

    def clone(self) -> "Document":
        return copy.deepcopy(self)


# ----------------------------------------------------------------- variables
_VAR = re.compile(r"\{([^{}:]+)(?::([^{}]*))?\}")


def builtin_vars(nr: int = 1, now: _dt.datetime | None = None) -> dict:
    now = now or _dt.datetime.now()
    return {
        "nr": nr,
        "datum": now.strftime("%d.%m.%Y"),
        "datum_kurz": now.strftime("%d.%m.%y"),
        "zeit": now.strftime("%H:%M"),
        "jahr": now.strftime("%Y"),
        "monat": now.strftime("%m"),
        "kw": now.strftime("%V"),
    }


def substitute(text: str, values: dict | None) -> str:
    """Replace {name} / {name:fmt} placeholders. Unknown names stay visible."""
    if not values or "{" not in text:
        return text
    lower = {str(k).strip().lower(): v for k, v in values.items()}

    def rep(m: re.Match) -> str:
        key, fmt = m.group(1).strip().lower(), m.group(2)
        if key not in lower:
            return m.group(0)
        v = lower[key]
        if fmt:
            try:
                if fmt.isdigit():  # {nr:3} -> 001
                    return str(int(v)).zfill(int(fmt))
                return format(int(v) if isinstance(v, int) else float(str(v).replace(",", ".")), fmt)
            except (ValueError, TypeError):
                return str(v)
        return str(v)

    return _VAR.sub(rep, text)


def placeholders(doc: Document) -> list[str]:
    names: list[str] = []
    for e in doc.elements:
        for key in ("text", "data"):
            v = e.props.get(key)
            if isinstance(v, str) and e.type != "image":
                for m in _VAR.finditer(v):
                    n = m.group(1).strip()
                    if n not in names:
                        names.append(n)
    return names
