"""Property panel for the selected element."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFontComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPlainTextEdit, QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget)

from .model import TYPE_NAMES, Element, snap_mm
from .render import BARCODES, encode_image_file, encode_pil


def dspin(lo, hi, step=0.5, dec=2, suffix=" mm"):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    s.setAccelerated(True)
    return s


class ElementPanel(QWidget):
    """Edits one element in place and emits *changed* after each edit."""

    changed = Signal(object, bool)   # element, commit (True = undo point)

    def __init__(self, placeholder_provider, parent=None):
        super().__init__(parent)
        self._placeholders = placeholder_provider
        self.el: Element | None = None
        self._loading = False
        self._commit_timer = QTimer(self, singleShot=True, interval=600)
        self._commit_timer.timeout.connect(lambda: self.el and self.changed.emit(self.el, True))
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._body: QWidget | None = None
        self.set_element(None)

    # ------------------------------------------------------------------ build
    def set_element(self, el: Element | None) -> None:
        if self._commit_timer.isActive():
            self._commit_timer.stop()
            if self.el is not None:
                self.changed.emit(self.el, True)
        self.el = el
        if self._body is not None:
            self._lay.removeWidget(self._body)
            self._body.hide()
            self._body.deleteLater()
        self._body = QWidget()
        self._lay.addWidget(self._body)
        form = QFormLayout(self._body)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignRight)
        self._form = form
        self._widgets = {}
        if el is None:
            hint = QLabel("Kein Element ausgewählt.\n\nFüge über die Werkzeugleiste Text, Bild, "
                          "QR-Code, Barcode oder Formen hinzu – oder ziehe ein Bild auf das Etikett.")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: palette(mid); padding: 8px;")
            form.addRow(hint)
            return
        self._loading = True
        title = QLabel(f"<b>{TYPE_NAMES.get(el.type, el.type)}</b>")
        form.addRow(title)
        getattr(self, f"_build_{el.type}", self._build_shape)(el, form)
        self._build_geometry(el, form)
        self._loading = False

    def refresh_geometry(self) -> None:
        if self.el is None or "x" not in self._widgets:
            return
        self._loading = True
        for k in ("x", "y", "w", "h"):
            self._widgets[k].setValue(getattr(self.el, k))
        self._loading = False

    # ------------------------------------------------------------------ helpers
    def _emit(self, commit_later=True):
        if self._loading or self.el is None:
            return
        self.changed.emit(self.el, False)
        if commit_later:
            self._commit_timer.start()

    def _prop_setter(self, key, conv=lambda v: v):
        def f(v):
            if self._loading or self.el is None:
                return
            self.el.props[key] = conv(v)
            self._emit()
        return f

    def _check(self, form, label, key):
        cb = QCheckBox(label)
        cb.setChecked(bool(self.el.p(key)))
        cb.toggled.connect(self._prop_setter(key, bool))
        form.addRow("", cb)
        return cb

    def _slider(self, form, label, key, lo, hi, scale=1.0):
        row = QHBoxLayout()
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(int(round(float(self.el.p(key)) * scale)))
        val = QLabel()
        val.setMinimumWidth(34)

        def upd(v):
            val.setText(f"{v / scale:.2f}" if scale != 1 else str(v))
        upd(s.value())
        s.valueChanged.connect(upd)
        s.valueChanged.connect(self._prop_setter(key, lambda v: v / scale if scale != 1 else int(v)))
        row.addWidget(s)
        row.addWidget(val)
        form.addRow(label, row)
        return s

    def _placeholder_button(self, insert):
        b = QToolButton()
        b.setText("{ }")
        b.setToolTip("Platzhalter einfügen")
        b.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(b)

        def fill():
            m.clear()
            for name, desc in (("datum", "Datum"), ("zeit", "Uhrzeit"), ("nr", "Laufende Nummer"),
                               ("nr:3", "Nummer 3-stellig (001)"), ("kw", "Kalenderwoche")):
                m.addAction(f"{{{name}}}  –  {desc}", lambda n=name: insert("{" + n + "}"))
            cols = self._placeholders()
            if cols:
                m.addSeparator()
                for c in cols:
                    m.addAction(f"{{{c}}}  –  CSV-Spalte", lambda n=c: insert("{" + n + "}"))
        m.aboutToShow.connect(fill)
        b.setMenu(m)
        return b

    # ------------------------------------------------------------------ geometry
    def _build_geometry(self, el, form):
        form.addRow(QLabel("<b>Position & Größe</b>"))
        for key, label, lo in (("x", "X", -100), ("y", "Y", -100), ("w", "Breite", 1), ("h", "Höhe", 1)):
            s = dspin(lo, 400, 0.5)
            s.setValue(getattr(el, key))

            def setter(v, key=key):
                if self._loading:
                    return
                setattr(self.el, key, snap_mm(v))
                self._emit()
            s.valueChanged.connect(setter)
            self._widgets[key] = s
        r1 = QHBoxLayout()
        r1.addWidget(self._widgets["x"])
        r1.addWidget(self._widgets["y"])
        form.addRow("X / Y", r1)
        r2 = QHBoxLayout()
        r2.addWidget(self._widgets["w"])
        r2.addWidget(self._widgets["h"])
        form.addRow("B / H", r2)
        rot = QComboBox()
        rot.setEditable(True)
        rot.addItems(["0", "90", "180", "270"])
        rot.setCurrentText(f"{el.rotation:g}")

        def set_rot(t):
            if self._loading:
                return
            try:
                self.el.rotation = float(t.replace(",", ".")) % 360
            except ValueError:
                return
            self._emit()
        rot.currentTextChanged.connect(set_rot)
        form.addRow("Drehung °", rot)
        lock = QCheckBox("Gesperrt (nicht verschiebbar)")
        lock.setChecked(el.locked)

        def set_lock(v):
            if self._loading:
                return
            self.el.locked = v
            self._emit()
        lock.toggled.connect(set_lock)
        form.addRow("", lock)

    # ------------------------------------------------------------------ types
    def _build_text(self, el, form):
        ed = QPlainTextEdit(el.p("text"))
        ed.setFixedHeight(76)
        ed.setPlaceholderText("Text, z. B. {Name} oder {datum}")
        ed.textChanged.connect(lambda: self._prop_setter("text")(ed.toPlainText()))
        row = QHBoxLayout()
        row.addWidget(ed)
        row.addWidget(self._placeholder_button(lambda s: ed.insertPlainText(s)), 0, Qt.AlignTop)
        form.addRow("Text", row)
        fc = QFontComboBox()
        fc.setCurrentFont(QFont(el.p("font")))
        fc.currentFontChanged.connect(lambda f: self._prop_setter("font")(f.family()))
        form.addRow("Schrift", fc)
        size = dspin(3, 200, 1, 1, " pt")
        size.setValue(float(el.p("size_pt")))
        size.valueChanged.connect(self._prop_setter("size_pt", float))
        style = QHBoxLayout()
        style.addWidget(size)
        for key, txt, tip in (("bold", "F", "Fett"), ("italic", "K", "Kursiv"), ("underline", "U", "Unterstrichen")):
            b = QToolButton()
            b.setText(txt)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setChecked(bool(el.p(key)))
            f = b.font()
            f.setBold(key == "bold")
            f.setItalic(key == "italic")
            f.setUnderline(key == "underline")
            b.setFont(f)
            b.toggled.connect(self._prop_setter(key, bool))
            style.addWidget(b)
        form.addRow("Größe", style)
        al = QHBoxLayout()
        grp = QButtonGroup(self)
        for val, txt in (("left", "⯇ Links"), ("center", "Mitte"), ("right", "Rechts ⯈")):
            b = QToolButton()
            b.setText(txt)
            b.setCheckable(True)
            b.setChecked(el.p("halign") == val)
            grp.addButton(b)
            b.clicked.connect(lambda _=False, v=val: self._prop_setter("halign")(v))
            al.addWidget(b)
        va = QComboBox()
        for val, txt in (("top", "oben"), ("center", "mittig"), ("bottom", "unten")):
            va.addItem(txt, val)
        va.setCurrentIndex(["top", "center", "bottom"].index(el.p("valign")))
        va.currentIndexChanged.connect(lambda i: self._prop_setter("valign")(va.itemData(i)))
        al.addWidget(va)
        form.addRow("Ausrichtung", al)
        ls = dspin(0.5, 3, 0.05, 2, "×")
        ls.setValue(float(el.p("line_spacing")))
        ls.valueChanged.connect(self._prop_setter("line_spacing", float))
        form.addRow("Zeilenabstand", ls)
        self._check(form, "Automatisch verkleinern, bis es passt", "autofit")
        self._check(form, "Invertiert (weiß auf schwarz)", "invert")

    def _build_image(self, el, form):
        row = QHBoxLayout()
        load = QPushButton("Bild laden …")
        paste = QPushButton("Einfügen")
        paste.setToolTip("Bild aus der Zwischenablage")
        row.addWidget(load)
        row.addWidget(paste)
        form.addRow("Bild", row)

        def do_load():
            fn, _ = QFileDialog.getOpenFileName(self, "Bild wählen", "",
                                                "Bilder (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)")
            if fn:
                self.el.props["data"] = encode_image_file(fn)
                self._emit()

        def do_paste():
            from PySide6.QtGui import QGuiApplication
            from PIL import ImageQt
            img = QGuiApplication.clipboard().image()
            if not img.isNull():
                self.el.props["data"] = encode_pil(ImageQt.fromqimage(img))
                self._emit()
        load.clicked.connect(do_load)
        paste.clicked.connect(do_paste)
        dith = QComboBox()
        dith.addItem("Fotos: Rasterung (Floyd–Steinberg)", "floyd")
        dith.addItem("Grafik/Logo: harte Schwelle", "threshold")
        dith.setCurrentIndex(0 if el.p("dither") == "floyd" else 1)
        dith.currentIndexChanged.connect(lambda i: self._prop_setter("dither")(dith.itemData(i)))
        form.addRow("Umwandlung", dith)
        self._slider(form, "Schwelle", "threshold", 1, 254)
        self._slider(form, "Helligkeit", "brightness", 20, 250, 100.0)
        self._slider(form, "Kontrast", "contrast", 20, 300, 100.0)
        self._check(form, "Seitenverhältnis beibehalten", "keep_aspect")
        self._check(form, "Invertieren", "invert")

    def _build_qr(self, el, form):
        ed = QLineEdit(el.p("data"))
        ed.textChanged.connect(self._prop_setter("data"))
        row = QHBoxLayout()
        row.addWidget(ed)
        row.addWidget(self._placeholder_button(lambda s: ed.insert(s)))
        form.addRow("Inhalt", row)
        ec = QComboBox()
        for v, t in (("L", "L – 7 %"), ("M", "M – 15 %"), ("Q", "Q – 25 %"), ("H", "H – 30 %")):
            ec.addItem(t, v)
        ec.setCurrentIndex("LMQH".index(el.p("ec")))
        ec.currentIndexChanged.connect(lambda i: self._prop_setter("ec")(ec.itemData(i)))
        form.addRow("Fehlerkorrektur", ec)

    def _build_barcode(self, el, form):
        sym = QComboBox()
        for k, v in BARCODES.items():
            sym.addItem(v, k)
        sym.setCurrentIndex(list(BARCODES).index(el.p("symbology")) if el.p("symbology") in BARCODES else 0)
        sym.currentIndexChanged.connect(lambda i: self._prop_setter("symbology")(sym.itemData(i)))
        form.addRow("Typ", sym)
        ed = QLineEdit(el.p("data"))
        ed.textChanged.connect(self._prop_setter("data"))
        row = QHBoxLayout()
        row.addWidget(ed)
        row.addWidget(self._placeholder_button(lambda s: ed.insert(s)))
        form.addRow("Inhalt", row)
        self._check(form, "Klartext darunter", "show_text")
        fs = dspin(4, 30, 0.5, 1, " pt")
        fs.setValue(float(el.p("font_pt")))
        fs.valueChanged.connect(self._prop_setter("font_pt", float))
        form.addRow("Klartext-Größe", fs)
        tip = QLabel("Tipp: Für sicheres Scannen mind. 2 Druckpunkte pro Strich – Barcode breit genug ziehen.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: palette(mid);")
        form.addRow(tip)

    def _build_shape(self, el, form):
        lw = dspin(0.125, 20, 0.125, 3)
        lw.setValue(float(el.p("line_mm")))
        lw.valueChanged.connect(self._prop_setter("line_mm", float))
        form.addRow("Linienstärke", lw)
        if el.type in ("rect", "ellipse"):
            self._check(form, "Gefüllt", "fill")
        if el.type == "rect":
            rr = dspin(0, 50, 0.5)
            rr.setValue(float(el.p("radius_mm")))
            rr.valueChanged.connect(self._prop_setter("radius_mm", float))
            form.addRow("Eckenradius", rr)

    _build_rect = _build_shape
    _build_ellipse = _build_shape
    _build_line = _build_shape
