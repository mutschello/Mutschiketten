"""Main window of Mutschiketten."""

from __future__ import annotations

import csv
import io
import json
import os
import re

from PySide6.QtCore import QByteArray, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDockWidget,
                               QFileDialog, QFormLayout, QFrame, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QTabWidget,
                               QToolBar, QToolButton, QVBoxLayout, QWidget)

from . import __version__
from .canvas import LabelScene, LabelView
from .model import DPMM, Document, Element, TYPE_NAMES, builtin_vars, placeholders, snap_mm
from .panels import ElementPanel, dspin
from .render import bool_to_qimage, encode_image_file, encode_pil, head_image
from .worker import start_worker

FILE_FILTER = "Etiketten (*.etikett);;Alle Dateien (*)"
PRESETS = [(40, 30), (50, 30), (40, 20), (50, 20), (30, 15), (40, 40), (40, 60), (50, 80), (25, 25), (14, 30)]
MAX_HISTORY = 100


def read_csv(path: str) -> tuple[list[str], list[dict]]:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if text.count(";") >= text.count(",") else ","
    rd = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rd]
    rows = [r for r in rows if any(r.values())]
    return [c.strip() for c in (rd.fieldnames or []) if c], rows


def parse_ranges(spec: str, n: int) -> list[int]:
    spec = spec.strip()
    if not spec or spec.lower() in ("alle", "all", "*"):
        return list(range(n))
    out: list[int] = []
    for part in re.split(r"[,; ]+", spec):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a else 1
            b = int(b) if b else n
            out += list(range(a - 1, min(b, n)))
        else:
            i = int(part) - 1
            if 0 <= i < n:
                out.append(i)
    return [i for i in out if 0 <= i < n]


class MainWindow(QMainWindow):
    request_print = Signal(object, int, str)

    def __init__(self):
        super().__init__()
        self.settings = QSettings("EtikettStudio", "EtikettStudio")
        self.path: str | None = None
        self.dirty = False
        self.history: list[str] = []
        self.hist_pos = -1
        self.csv_cols: list[str] = []
        self.csv_rows: list[dict] = []
        self.csv_path = ""
        self.material = None
        self._clip: list[dict] = []

        self.scene = LabelScene(self)
        self.view = LabelView(self.scene)
        self.setCentralWidget(self.view)
        self.scene.selectionChanged.connect(self.on_selection)
        self.scene.element_changed.connect(lambda el: self.commit())
        self.scene.element_live.connect(self.on_live)
        self.view.files_dropped.connect(self.on_files_dropped)
        self.view.arrow_pressed.connect(self.on_arrow)

        self._build_actions()
        self._build_toolbar()
        self._build_dock()
        self._build_statusbar()
        self._build_menu()

        self.worker, self.thread = start_worker(self)
        self.worker.status.connect(self.on_status)
        self.worker.message.connect(self.on_printer_message)
        self.worker.progress.connect(lambda m: self.job_label.setText(m))
        self.worker.finished.connect(self.on_print_finished)
        self.request_print.connect(self.worker.print_job)

        geo = self.settings.value("geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        else:
            self.resize(1280, 800)
        last = self.settings.value("last_document", "")
        doc = None
        if last:
            try:
                doc = Document.from_json(last)
            except Exception:  # noqa: BLE001
                doc = None
        self.path = self.settings.value("last_path", "") or None
        self.load_document(doc or self.example_document(), reset_history=True)
        if not doc:
            self.path = None
        self.dirty = False
        self.update_title()
        QTimer.singleShot(0, self.view.fit)

    # ================================================================ building
    def _act(self, text, slot, shortcut=None, tip=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_new = A("Neu", self.file_new, QKeySequence.New)
        self.a_open = A("Öffnen …", self.file_open, QKeySequence.Open)
        self.a_save = A("Speichern", self.file_save, QKeySequence.Save)
        self.a_saveas = A("Speichern unter …", self.file_save_as, QKeySequence.SaveAs)
        self.a_png = A("Als PNG exportieren …", self.export_png)
        self.a_undo = A("Rückgängig", self.undo, QKeySequence.Undo)
        self.a_redo = A("Wiederholen", self.redo, QKeySequence.Redo)
        self.a_copy = A("Kopieren", self.copy_sel, QKeySequence.Copy)
        self.a_paste = A("Einfügen", self.paste, QKeySequence.Paste)
        self.a_dup = A("Duplizieren", self.duplicate, "Ctrl+D")
        self.a_del = A("Löschen", self.delete_sel, QKeySequence.Delete)
        self.a_selall = A("Alles auswählen", self.select_all, QKeySequence.SelectAll)
        self.a_front = A("Nach vorne", lambda: self.z_order(1), "Ctrl+]")
        self.a_back = A("Nach hinten", lambda: self.z_order(-1), "Ctrl+[")
        self.a_preview = A("1-Bit-Vorschau", self.toggle_preview, "Ctrl+B",
                           "Zeigt exakt, was der Drucker druckt", checkable=True)
        self.a_fit = A("Einpassen", self.view.fit, "Ctrl+0")
        self.a_zin = A("Vergrößern", lambda: self.view.zoom(1.25), QKeySequence.ZoomIn)
        self.a_zout = A("Verkleinern", lambda: self.view.zoom(0.8), QKeySequence.ZoomOut)
        self.a_print = A("Drucken", self.print_now, QKeySequence.Print, "Etikett(en) drucken")
        self.adds = {}
        for t, key in (("text", "T"), ("image", "I"), ("qr", "Q"), ("barcode", "B"), ("rect", "R"),
                       ("ellipse", "E"), ("line", "L")):
            self.adds[t] = A(TYPE_NAMES[t], lambda _=False, t=t: self.add_element(t), None,
                             f"{TYPE_NAMES[t]} hinzufügen")
        self.aligns = {}
        for k, txt in (("left", "⇤ Links"), ("hcenter", "↔ Mitte"), ("right", "Rechts ⇥"),
                       ("top", "⤒ Oben"), ("vcenter", "↕ Mitte"), ("bottom", "Unten ⤓")):
            self.aligns[k] = A(txt, lambda _=False, k=k: self.align(k), None,
                               "Am Etikett ausrichten (bei Mehrfachauswahl: aneinander)")

    def _build_toolbar(self):
        tb = QToolBar("Werkzeuge")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)
        for a in (self.a_new, self.a_open, self.a_save):
            tb.addAction(a)
        tb.addSeparator()
        self.a_undo.setIconText("↶")
        self.a_redo.setIconText("↷")
        self.a_undo.setToolTip("Rückgängig (Strg+Z)")
        self.a_redo.setToolTip("Wiederholen (Strg+Y)")
        for a in (self.a_undo, self.a_redo):
            tb.addAction(a)
        tb.addSeparator()
        lab = QLabel("  Einfügen: ")
        lab.setStyleSheet("color: palette(mid);")
        tb.addWidget(lab)
        for t in ("text", "image", "qr", "barcode"):
            tb.addAction(self.adds[t])
        shapes = QToolButton()
        shapes.setText("Formen ▾")
        shapes.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(shapes)
        for t in ("rect", "ellipse", "line"):
            m.addAction(self.adds[t])
        shapes.setMenu(m)
        tb.addWidget(shapes)
        tb.addSeparator()
        al = QToolButton()
        al.setText("Ausrichten ▾")
        al.setPopupMode(QToolButton.InstantPopup)
        m2 = QMenu(al)
        for k in ("left", "hcenter", "right"):
            m2.addAction(self.aligns[k])
        m2.addSeparator()
        for k in ("top", "vcenter", "bottom"):
            m2.addAction(self.aligns[k])
        m2.addSeparator()
        m2.addAction(self.a_front)
        m2.addAction(self.a_back)
        al.setMenu(m2)
        tb.addWidget(al)
        tb.addAction(self.a_del)
        tb.addSeparator()
        tb.addAction(self.a_preview)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.print_btn = QPushButton("  Drucken  ")
        self.print_btn.setObjectName("printButton")
        self.print_btn.clicked.connect(self.print_now)
        tb.addWidget(self.print_btn)

    def _build_menu(self):
        mb = self.menuBar()
        f = mb.addMenu("&Datei")
        for a in (self.a_new, self.a_open):
            f.addAction(a)
        self.recent_menu = f.addMenu("Zuletzt geöffnet")
        self.recent_menu.aboutToShow.connect(self.fill_recent)
        f.addSeparator()
        for a in (self.a_save, self.a_saveas, self.a_png):
            f.addAction(a)
        f.addSeparator()
        f.addAction(self.a_print)
        f.addAction("Testdruck (Ausrichtung prüfen)", self.test_print)
        f.addSeparator()
        f.addAction("Beenden", self.close)
        e = mb.addMenu("&Bearbeiten")
        for a in (self.a_undo, self.a_redo, None, self.a_copy, self.a_paste, self.a_dup, self.a_del,
                  None, self.a_selall, None, self.a_front, self.a_back):
            e.addSeparator() if a is None else e.addAction(a)
        i = mb.addMenu("&Einfügen")
        for a in self.adds.values():
            i.addAction(a)
        v = mb.addMenu("&Ansicht")
        for a in (self.a_preview, self.a_fit, self.a_zin, self.a_zout):
            v.addAction(a)
        v.addSeparator()
        self.a_grid = self._act("Raster anzeigen", self.toggle_grid, checkable=True)
        self.a_snap = self._act("Magnetische Hilfslinien", self.toggle_snap, checkable=True)
        self.a_snap.setChecked(True)
        self.a_frames = self._act("Elementrahmen anzeigen", self.toggle_frames, checkable=True)
        self.a_frames.setChecked(True)
        for a in (self.a_grid, self.a_snap, self.a_frames):
            v.addAction(a)
        h = mb.addMenu("&Hilfe")
        h.addAction("Tastenkürzel & Tipps", self.show_help)
        h.addAction("Über Mutschiketten", self.show_about)

    def _scroll(self, w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(w)
        lay.addStretch(1)
        sa.setWidget(wrap)
        return sa

    def _build_dock(self):
        dock = QDockWidget("Eigenschaften", self)
        dock.setObjectName("props_dock")
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        dock.setTitleBarWidget(QWidget())
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(360)
        self.panel = ElementPanel(lambda: self.csv_cols)
        self.panel.changed.connect(self.on_panel_changed)
        self.tabs.addTab(self._scroll(self.panel), "Element")
        self.tabs.addTab(self._scroll(self._build_label_tab()), "Etikett")
        self.tabs.addTab(self._scroll(self._build_print_tab()), "Drucken")
        dock.setWidget(self.tabs)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    def _build_label_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        g = QGroupBox("Etikettenformat")
        f = QFormLayout(g)
        self.preset = QComboBox()
        self.preset.addItem("Benutzerdefiniert", None)
        for a, b in PRESETS:
            self.preset.addItem(f"{a} × {b} mm", (a, b))
        self.preset.activated.connect(self.on_preset)
        f.addRow("Vorlage", self.preset)
        self.sp_w = dspin(5, 50, 1, 1)
        self.sp_l = dspin(5, 300, 1, 1)
        self.sp_w.setToolTip("Breite quer zum Druckkopf (max. 48 mm bedruckbar)")
        self.sp_l.setToolTip("Länge in Vorschubrichtung")
        self.sp_w.valueChanged.connect(self.on_label_size)
        self.sp_l.valueChanged.connect(self.on_label_size)
        f.addRow("Breite (quer)", self.sp_w)
        f.addRow("Länge (Vorschub)", self.sp_l)
        self.cb_land = QCheckBox("Querformat (um 90° gedreht gestalten)")
        self.cb_land.toggled.connect(self.on_label_size)
        f.addRow("", self.cb_land)
        self.mat_label = QLabel("Eingelegte Rolle: –")
        self.mat_label.setWordWrap(True)
        self.btn_mat = QPushButton("Größe vom Drucker übernehmen")
        self.btn_mat.clicked.connect(self.apply_material)
        self.btn_mat.setEnabled(False)
        f.addRow(self.mat_label)
        f.addRow(self.btn_mat)
        lay.addWidget(g)
        info = QLabel("Rot schraffiert = nicht bedruckbar (je 1 mm am Anfang/Ende, "
                      "und alles über 48 mm Breite).")
        info.setWordWrap(True)
        info.setStyleSheet("color: palette(mid);")
        lay.addWidget(info)
        return w

    def _build_print_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("Druck")
        f = QFormLayout(g)
        row = QHBoxLayout()
        self.sl_density = QSlider(Qt.Horizontal)
        self.sl_density.setRange(0, 15)
        self.lb_density = QLabel()
        self.lb_density.setMinimumWidth(24)
        self.sl_density.valueChanged.connect(lambda v: (self.lb_density.setText(str(v)), self._doc_setting("density", v)))
        row.addWidget(self.sl_density)
        row.addWidget(self.lb_density)
        f.addRow("Druckdichte", row)
        self.sp_copies = QSpinBox()
        self.sp_copies.setRange(1, 999)
        self.sp_copies.valueChanged.connect(lambda v: self._doc_setting("copies", v))
        f.addRow("Kopien je Etikett", self.sp_copies)
        lay.addWidget(g)

        g2 = QGroupBox("Serie")
        f2 = QFormLayout(g2)
        self.mode_grp = QButtonGroup(self)
        modes = QHBoxLayout()
        for i, t in enumerate(("Einzeln", "Nummeriert", "CSV-Liste")):
            rb = QRadioButton(t)
            self.mode_grp.addButton(rb, i)
            modes.addWidget(rb)
        self.mode_grp.button(0).setChecked(True)
        self.mode_grp.idToggled.connect(lambda *_: self.update_mode())
        f2.addRow(modes)
        self.sp_start = QSpinBox()
        self.sp_start.setRange(-999999, 999999)
        self.sp_start.valueChanged.connect(lambda v: (self._doc_setting("counter_start", v), self.update_values()))
        self.sp_step = QSpinBox()
        self.sp_step.setRange(-1000, 1000)
        self.sp_step.valueChanged.connect(lambda v: self._doc_setting("counter_step", v))
        self.sp_count = QSpinBox()
        self.sp_count.setRange(1, 9999)
        self.sp_count.setValue(10)
        f2.addRow("{nr} beginnt bei", self.sp_start)
        f2.addRow("Schrittweite", self.sp_step)
        self.lb_count = QLabel("Anzahl Etiketten")
        f2.addRow(self.lb_count, self.sp_count)
        crow = QHBoxLayout()
        self.btn_csv = QPushButton("CSV laden …")
        self.btn_csv.clicked.connect(self.load_csv)
        self.lb_csv = QLabel("keine Datei")
        self.lb_csv.setWordWrap(True)
        crow.addWidget(self.btn_csv)
        crow.addWidget(self.lb_csv, 1)
        self.w_csvrow = QWidget()
        self.w_csvrow.setLayout(crow)
        crow.setContentsMargins(0, 0, 0, 0)
        f2.addRow(self.w_csvrow)
        self.ed_rows = QLineEdit()
        self.ed_rows.setPlaceholderText("alle   (oder z. B. 1-5, 8, 12)")
        self.lb_rows = QLabel("Zeilen")
        f2.addRow(self.lb_rows, self.ed_rows)
        self.sp_preview = QSpinBox()
        self.sp_preview.setRange(1, 1)
        self.sp_preview.valueChanged.connect(lambda _: self.update_values())
        self.lb_preview = QLabel("Vorschau-Zeile")
        f2.addRow(self.lb_preview, self.sp_preview)
        self.lb_csvhint = QLabel("Spalten werden als {Spaltenname} eingesetzt. Eine Spalte „Anzahl“ "
                                 "legt die Stückzahl pro Zeile fest.")
        self.lb_csvhint.setWordWrap(True)
        self.lb_csvhint.setStyleSheet("color: palette(mid);")
        f2.addRow(self.lb_csvhint)
        lay.addWidget(g2)

        g3 = QGroupBox("Kalibrierung")
        f3 = QFormLayout(g3)
        self.cb_rot180 = QCheckBox("Ausgabe um 180° drehen")
        self.cb_rot180.setChecked(self.settings.value("rotate180", False, type=bool))
        self.cb_rot180.toggled.connect(lambda v: self.settings.setValue("rotate180", v))
        f3.addRow("", self.cb_rot180)
        self.cb_landcw = QCheckBox("Querformat im Uhrzeigersinn")
        self.cb_landcw.setChecked(self.settings.value("landscape_cw", True, type=bool))
        self.cb_landcw.toggled.connect(lambda v: self.settings.setValue("landscape_cw", v))
        f3.addRow("", self.cb_landcw)
        orow = QHBoxLayout()
        self.sp_offx = dspin(-10, 10, 0.125, 3)
        self.sp_offy = dspin(-10, 10, 0.125, 3)
        self.sp_offx.setValue(self.settings.value("offset_x", 0.0, type=float))
        self.sp_offy.setValue(self.settings.value("offset_y", 0.0, type=float))
        self.sp_offx.valueChanged.connect(lambda v: self.settings.setValue("offset_x", v))
        self.sp_offy.valueChanged.connect(lambda v: self.settings.setValue("offset_y", v))
        orow.addWidget(self.sp_offx)
        orow.addWidget(self.sp_offy)
        f3.addRow("Versatz X / Y", orow)
        self.sl_thr = QSlider(Qt.Horizontal)
        self.sl_thr.setRange(40, 220)
        self.sl_thr.setValue(self.settings.value("threshold", 128, type=int))
        self.sl_thr.setToolTip("Ab welchem Grauwert Text/Kanten schwarz werden (höher = fetter)")
        self.sl_thr.valueChanged.connect(lambda v: (self.settings.setValue("threshold", v), self.refresh_preview()))
        f3.addRow("Kantenschärfe", self.sl_thr)
        self.cb_method = QComboBox()
        self.cb_method.addItem("Optimiert (ohne LZMA-Endmarker)", "native")
        self.cb_method.addItem("Kompatibel (Python-LZMA)", "stdlib")
        self.cb_method.setCurrentIndex(0 if self.settings.value("method", "native") == "native" else 1)
        self.cb_method.currentIndexChanged.connect(
            lambda i: self.settings.setValue("method", self.cb_method.itemData(i)))
        self.cb_method.setToolTip("Falls lange Etiketten unvollständig gedruckt werden: andere Variante probieren.")
        f3.addRow("Übertragung", self.cb_method)
        tbtn = QPushButton("Testdruck")
        tbtn.clicked.connect(self.test_print)
        f3.addRow(tbtn)
        lay.addWidget(g3)

        self.job_label = QLabel("")
        self.job_label.setWordWrap(True)
        lay.addWidget(self.job_label)
        brow = QHBoxLayout()
        self.btn_print2 = QPushButton("Drucken")
        self.btn_print2.setObjectName("printButton")
        self.btn_print2.clicked.connect(self.print_now)
        self.btn_cancel = QPushButton("Abbrechen")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(lambda: self.worker.cancel.set())
        brow.addWidget(self.btn_print2, 1)
        brow.addWidget(self.btn_cancel)
        lay.addLayout(brow)
        self.update_mode()
        return w

    def _build_statusbar(self):
        sb = self.statusBar()
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #9ca3af; font-size: 14px;")
        self.st_label = QLabel("Suche Drucker …")
        self.zoom_label = QLabel("")
        sb.addWidget(self.dot)
        sb.addWidget(self.st_label, 1)
        sb.addPermanentWidget(self.zoom_label)
        self.view.zoom_changed.connect(lambda z: self.zoom_label.setText(f"Zoom {z / (96 / 25.4 / DPMM) * 100:.0f} %"))

    # ================================================================ document
    def example_document(self) -> Document:
        d = Document(width_mm=40, length_mm=30)
        d.elements = [
            Element.new("text", x=3, y=3, w=34, h=9, props={"text": "Mutschiketten", "size_pt": 16, "bold": True}),
            Element.new("line", x=3, y=13, w=34, h=1, props={"line_mm": 0.25}),
            Element.new("text", x=3, y=15, w=20, h=11, props={"text": "Nr. {nr:3}\n{datum}", "size_pt": 9,
                                                               "halign": "left"}),
            Element.new("qr", x=26, y=15, w=11, h=11, props={"data": "https://claude.ai"}),
        ]
        return d

    def load_document(self, doc: Document, reset_history=False):
        self.doc = doc
        self.scene.set_document(doc)
        self._loading_ui = True
        self.sp_w.setValue(doc.width_mm)
        self.sp_l.setValue(doc.length_mm)
        self.cb_land.setChecked(doc.landscape)
        self.sl_density.setValue(doc.density)
        self.lb_density.setText(str(doc.density))
        self.sp_copies.setValue(doc.copies)
        self.sp_start.setValue(doc.counter_start)
        self.sp_step.setValue(doc.counter_step)
        self._sync_preset()
        self._loading_ui = False
        self.update_values()
        if reset_history:
            self.history = [doc.to_json()]
            self.hist_pos = 0
        self.on_selection()

    def _doc_setting(self, key, value):
        if getattr(self, "_loading_ui", False) or not hasattr(self, "doc"):
            return
        if getattr(self.doc, key) != value:
            setattr(self.doc, key, value)
            self.mark_dirty()

    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self.update_title()
        if self.a_preview.isChecked():
            self.refresh_preview()

    def update_title(self):
        name = os.path.basename(self.path) if self.path else "Unbenannt"
        self.setWindowTitle(f"{'• ' if self.dirty else ''}{name} – Mutschiketten")

    def commit(self):
        """Push an undo snapshot of the current document."""
        js = self.doc.to_json()
        if self.history and self.history[self.hist_pos] == js:
            return
        self.history = self.history[: self.hist_pos + 1] + [js]
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]
        self.hist_pos = len(self.history) - 1
        self.mark_dirty()
        self.panel.refresh_geometry()

    def _restore(self, js: str):
        sel = [e.id for e in self.scene.selected_elements()]
        self.load_document(Document.from_json(js))
        for i in sel:
            if i in self.scene.items_by_id:
                self.scene.items_by_id[i].setSelected(True)
        self.mark_dirty()

    def undo(self):
        if self.hist_pos > 0:
            self.hist_pos -= 1
            self._restore(self.history[self.hist_pos])

    def redo(self):
        if self.hist_pos < len(self.history) - 1:
            self.hist_pos += 1
            self._restore(self.history[self.hist_pos])

    # ================================================================ values / preview
    def current_values(self, csv_index: int | None = None, nr: int | None = None) -> dict:
        v = builtin_vars(self.doc.counter_start if nr is None else nr)
        if self.mode_grp.checkedId() == 2 and self.csv_rows:
            i = self.sp_preview.value() - 1 if csv_index is None else csv_index
            if 0 <= i < len(self.csv_rows):
                v.update(self.csv_rows[i])
        return v

    def update_values(self):
        if not hasattr(self, "doc"):
            return
        self.scene.values = self.current_values()
        self.scene.update()
        if self.a_preview.isChecked():
            self.refresh_preview()

    def toggle_preview(self):
        if self.a_preview.isChecked():
            self.refresh_preview()
            self.scene.clearSelection()
        else:
            self.scene.set_preview(None)

    def refresh_preview(self):
        if not self.a_preview.isChecked():
            return
        doc = self.doc
        lc = doc.landscape
        try:
            arr = head_image(doc, self.scene.values,
                             offset_mm=(self.sp_offx.value(), self.sp_offy.value()),
                             threshold=self.sl_thr.value())
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage(f"Vorschau fehlgeschlagen: {e}", 4000)
            return
        import numpy as np
        if lc:  # back to design orientation
            arr = np.rot90(arr, k=1 if self.cb_landcw.isChecked() else -1)
        self.scene.set_preview(QPixmap.fromImage(bool_to_qimage(arr)))

    # ================================================================ selection / editing
    def on_selection(self):
        self.scene.guides = []
        sel = self.scene.selected_elements()
        self.panel.set_element(sel[0] if len(sel) == 1 else None)
        if len(sel) == 1 and self.tabs.currentIndex() != 0 and not getattr(self, "_loading_ui", False):
            self.tabs.setCurrentIndex(0)

    def on_live(self, el):
        if self.panel.el is el:
            self.panel.refresh_geometry()

    def on_panel_changed(self, el, commit):
        it = self.scene.items_by_id.get(el.id)
        if it:
            it.sync_from_model()
        if commit:
            self.commit()
        else:
            self.mark_dirty()

    def _center_new(self, el: Element):
        el.w = min(el.w, max(4.0, self.doc.design_w - 4))
        el.h = min(el.h, max(3.0, self.doc.design_h - 4))
        el.x = snap_mm((self.doc.design_w - el.w) / 2)
        el.y = snap_mm((self.doc.design_h - el.h) / 2)

    def add_element(self, t: str, **kw) -> Element:
        sizes = {"text": (30, 8), "image": (20, 20), "qr": (16, 16), "barcode": (34, 12), "rect": (20, 12),
                 "ellipse": (16, 12), "line": (30, 1)}
        w, h = sizes[t]
        el = Element.new(t, w=w, h=h, **kw)
        if t == "qr":
            s = min(16.0, self.doc.design_w - 4, self.doc.design_h - 4)
            el.w = el.h = max(6.0, s)
        self._center_new(el)
        if t == "image" and not el.props.get("data") and not kw:
            fn, _ = QFileDialog.getOpenFileName(self, "Bild wählen", self.settings.value("last_dir", ""),
                                                "Bilder (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)")
            if not fn:
                return el
            self.settings.setValue("last_dir", os.path.dirname(fn))
            el.props["data"] = encode_image_file(fn)
            self._fit_image(el)
        self._insert(el)
        return el

    def _fit_image(self, el: Element):
        from .render import decode_image
        img = decode_image(el.props.get("data", ""))
        if img is None:
            return
        maxw, maxh = self.doc.design_w - 4, self.doc.design_h - 4
        s = min(maxw / img.width, maxh / img.height)
        el.w, el.h = snap_mm(max(2, img.width * s)), snap_mm(max(2, img.height * s))
        self._center_new(el)

    def _insert(self, el: Element, select=True):
        self.doc.elements.append(el)
        self.scene.set_document(self.doc)
        if select:
            self.scene.clearSelection()
            self.scene.items_by_id[el.id].setSelected(True)
        self.commit()

    def delete_sel(self):
        if self.view.hasFocus() is False and QApplication.focusWidget() not in (None, self.view):
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit,)) or fw.inherits("QAbstractSpinBox") or fw.inherits("QPlainTextEdit"):
                return  # let the widget handle Delete
        ids = {e.id for e in self.scene.selected_elements()}
        if not ids:
            return
        self.doc.elements = [e for e in self.doc.elements if e.id not in ids]
        self.scene.set_document(self.doc)
        self.commit()

    def select_all(self):
        fw = QApplication.focusWidget()
        if fw is not None and fw is not self.view and (isinstance(fw, QLineEdit) or fw.inherits("QPlainTextEdit")):
            fw.selectAll()
            return
        for it in self.scene.items_by_id.values():
            it.setSelected(True)

    def duplicate(self):
        new = []
        for e in self.scene.selected_elements():
            c = e.clone()
            c.x, c.y = snap_mm(c.x + 2), snap_mm(c.y + 2)
            new.append(c)
        if not new:
            return
        self.doc.elements += new
        self.scene.set_document(self.doc)
        self.scene.clearSelection()
        for c in new:
            self.scene.items_by_id[c.id].setSelected(True)
        self.commit()

    def copy_sel(self):
        fw = QApplication.focusWidget()
        if fw is not None and fw is not self.view and (isinstance(fw, QLineEdit) or fw.inherits("QPlainTextEdit")):
            fw.copy()
            return
        from dataclasses import asdict
        self._clip = [asdict(e) for e in self.scene.selected_elements()]

    def paste(self):
        fw = QApplication.focusWidget()
        if fw is not None and fw is not self.view and (isinstance(fw, QLineEdit) or fw.inherits("QPlainTextEdit")):
            fw.paste()
            return
        cb = QGuiApplication.clipboard()
        md = cb.mimeData()
        if md.hasImage():
            from PIL import ImageQt
            img = cb.image()
            if not img.isNull():
                el = Element.new("image", props={"data": encode_pil(ImageQt.fromqimage(img)), "dither": "floyd"})
                self._fit_image(el)
                self._insert(el)
                return
        if md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
            self.on_files_dropped([u.toLocalFile() for u in md.urls() if u.isLocalFile()])
            return
        if self._clip:
            new = []
            for d in self._clip:
                e = Element(**d).clone()
                e.x, e.y = snap_mm(e.x + 2), snap_mm(e.y + 2)
                new.append(e)
            self._clip = [dict(d, x=d["x"] + 2, y=d["y"] + 2) for d in self._clip]
            self.doc.elements += new
            self.scene.set_document(self.doc)
            self.scene.clearSelection()
            for c in new:
                self.scene.items_by_id[c.id].setSelected(True)
            self.commit()
            return
        if md.hasText() and md.text().strip():
            self.add_element("text", props={"text": md.text().strip()})

    def z_order(self, d: int):
        sel = {e.id for e in self.scene.selected_elements()}
        if not sel:
            return
        els = self.doc.elements
        if d > 0:
            els[:] = [e for e in els if e.id not in sel] + [e for e in els if e.id in sel]
        else:
            els[:] = [e for e in els if e.id in sel] + [e for e in els if e.id not in sel]
        self.scene.set_document(self.doc)
        self.commit()

    def align(self, how: str):
        sel = self.scene.selected_elements()
        if not sel:
            return
        if len(sel) == 1:
            x0, y0, x1, y1 = 0, 0, self.doc.design_w, self.doc.design_h
        else:
            x0, y0 = min(e.x for e in sel), min(e.y for e in sel)
            x1, y1 = max(e.x + e.w for e in sel), max(e.y + e.h for e in sel)
        for e in sel:
            if how == "left":
                e.x = x0
            elif how == "right":
                e.x = x1 - e.w
            elif how == "hcenter":
                e.x = (x0 + x1 - e.w) / 2
            elif how == "top":
                e.y = y0
            elif how == "bottom":
                e.y = y1 - e.h
            elif how == "vcenter":
                e.y = (y0 + y1 - e.h) / 2
            e.x, e.y = snap_mm(e.x), snap_mm(e.y)
            self.scene.items_by_id[e.id].sync_from_model()
        self.commit()

    def on_arrow(self, dx: int, dy: int, mods) -> None:
        sel = self.scene.selected_elements()
        if sel:
            step = 0.125 if mods & Qt.AltModifier else (2.0 if mods & Qt.ShiftModifier else 0.5)
            for el in sel:
                if el.locked:
                    continue
                el.x, el.y = snap_mm(el.x + dx * step), snap_mm(el.y + dy * step)
                self.scene.items_by_id[el.id].sync_from_model()
            self.panel.refresh_geometry()
            self._arrow_commit.start()

    @property
    def _arrow_commit(self) -> QTimer:
        if not hasattr(self, "_arrow_timer"):
            self._arrow_timer = QTimer(self, singleShot=True, interval=500)
            self._arrow_timer.timeout.connect(self.commit)
        return self._arrow_timer

    def on_files_dropped(self, files: list[str]):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext == ".etikett":
                if self.maybe_save():
                    self.open_path(fn)
                return
            if ext == ".csv":
                self.load_csv(fn)
                continue
            try:
                el = Element.new("image", props={"data": encode_image_file(fn)})
            except Exception:  # noqa: BLE001
                continue
            self._fit_image(el)
            self._insert(el)

    # ================================================================ label format
    def _sync_preset(self):
        key = (int(self.doc.width_mm), int(self.doc.length_mm))
        idx = 0
        for i in range(1, self.preset.count()):
            if self.preset.itemData(i) == key and self.doc.width_mm == key[0] and self.doc.length_mm == key[1]:
                idx = i
        self.preset.setCurrentIndex(idx)

    def on_preset(self, i):
        d = self.preset.itemData(i)
        if d:
            self._loading_ui = True
            self.sp_w.setValue(d[0])
            self.sp_l.setValue(d[1])
            self._loading_ui = False
            self.on_label_size()

    def on_label_size(self, *_):
        if getattr(self, "_loading_ui", False):
            return
        old_w, old_h = self.doc.design_w, self.doc.design_h
        self.doc.width_mm = self.sp_w.value()
        self.doc.length_mm = self.sp_l.value()
        self.doc.landscape = self.cb_land.isChecked()
        # keep elements in the same relative position when the canvas changes
        nw, nh = self.doc.design_w, self.doc.design_h
        if (nw, nh) != (old_w, old_h) and old_w and old_h:
            for e in self.doc.elements:
                cx = (e.x + e.w / 2) / old_w * nw
                cy = (e.y + e.h / 2) / old_h * nh
                e.x, e.y = snap_mm(cx - e.w / 2), snap_mm(cy - e.h / 2)
        self.scene.set_document(self.doc)
        self._sync_preset()
        self.view.fit()
        self.commit()

    def apply_material(self):
        m = self.material
        if not m or not m.width_mm:
            return
        self._loading_ui = True
        self.sp_w.setValue(min(50, m.width_mm))
        self.sp_l.setValue(m.length_mm or self.doc.length_mm)
        self._loading_ui = False
        self.on_label_size()

    # ================================================================ printer status
    def on_status(self, st, mat):
        if st is None:
            self.dot.setStyleSheet("color: #9ca3af; font-size: 14px;")
            self.btn_mat.setEnabled(False)
            return
        errs, warns = st.errors(), st.warnings()
        if errs:
            color, text = "#dc2626", " · ".join(errs)
        elif st.printing or st.busy:
            color, text = "#2563eb", "Druckt …"
        else:
            color, text = "#16a34a", "Bereit"
        if warns:
            text += "  ·  " + " · ".join(warns)
            if not errs:
                color = "#d97706"
        self.dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        if mat and mat.width_mm:
            ln = f"{mat.length_mm}" if mat.length_mm else "endlos"
            mtxt = f"{mat.width_mm} × {ln} mm"
            if mat.gap_mm:
                mtxt += f", Abstand {mat.gap_mm} mm"
            self.mat_label.setText(f"Eingelegte Rolle: <b>{mtxt}</b>")
            first = self.material is None or (self.material.width_mm, self.material.length_mm) != (mat.width_mm, mat.length_mm)
            self.material = mat
            self.btn_mat.setEnabled(True)
            text = f"T50M Pro – {text}  ·  Rolle {mtxt}"
            if first and (abs(mat.width_mm - self.doc.width_mm) > 0.5 or
                          (mat.length_mm and abs(mat.length_mm - self.doc.length_mm) > 0.5)):
                self.statusBar().showMessage(
                    f"Hinweis: Eingelegte Rolle ({mtxt}) passt nicht zum Etikett "
                    f"({self.doc.width_mm:g} × {self.doc.length_mm:g} mm) – Reiter „Etikett“ → übernehmen.", 10000)
        else:
            text = f"T50M Pro – {text}"
        self.st_label.setText(text)

    def on_printer_message(self, msg: str):
        self.st_label.setText(msg)

    # ================================================================ printing
    def update_mode(self):
        m = self.mode_grp.checkedId()
        for w in (self.sp_start, self.sp_step):
            w.setEnabled(m != 0)
        self.sp_count.setVisible(m == 1)
        self.lb_count.setVisible(m == 1)
        for w in (self.w_csvrow, self.ed_rows, self.lb_rows, self.sp_preview, self.lb_preview, self.lb_csvhint):
            w.setVisible(m == 2)
        self.update_values()

    def load_csv(self, fn: str | None = None):
        if not fn:
            fn, _ = QFileDialog.getOpenFileName(self, "CSV-Datei wählen", self.settings.value("last_dir", ""),
                                                "CSV (*.csv *.txt);;Alle Dateien (*)")
        if not fn:
            return
        try:
            cols, rows = read_csv(fn)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "CSV", f"Datei konnte nicht gelesen werden:\n{e}")
            return
        self.csv_cols, self.csv_rows, self.csv_path = cols, rows, fn
        self.lb_csv.setText(f"<b>{os.path.basename(fn)}</b><br>{len(rows)} Zeilen · "
                            + ", ".join("{" + c + "}" for c in cols[:8]) + (" …" if len(cols) > 8 else ""))
        self.sp_preview.setRange(1, max(1, len(rows)))
        self.mode_grp.button(2).setChecked(True)
        self.tabs.setCurrentIndex(2)
        self.update_mode()
        missing = [p for p in placeholders(self.doc) if p.split(":")[0].lower() not in
                   {c.lower() for c in cols} | set(builtin_vars())]
        if missing:
            self.statusBar().showMessage("Platzhalter ohne passende Spalte: " + ", ".join(missing), 8000)

    def build_jobs(self) -> list[dict]:
        """List of value dicts, one per printed label (copies expanded)."""
        m = self.mode_grp.checkedId()
        copies = self.sp_copies.value()
        start, step = self.sp_start.value(), self.sp_step.value()
        jobs: list[dict] = []
        if m == 0:
            jobs = [builtin_vars(start)] * copies
        elif m == 1:
            for i in range(self.sp_count.value()):
                jobs += [builtin_vars(start + i * step)] * copies
        else:
            if not self.csv_rows:
                raise ValueError("Bitte zuerst eine CSV-Datei laden.")
            idx = parse_ranges(self.ed_rows.text(), len(self.csv_rows))
            qty_col = next((c for c in self.csv_cols if c.lower() in ("anzahl", "menge", "stück", "stueck", "qty")), None)
            for n, i in enumerate(idx):
                row = self.csv_rows[i]
                v = builtin_vars(start + n * step)
                v.update(row)
                k = copies
                if qty_col:
                    try:
                        k = max(0, int(float(row.get(qty_col, "").replace(",", ".") or copies)))
                    except ValueError:
                        k = copies
                jobs += [v] * k
        return jobs

    def _render_jobs(self, jobs: list[dict], doc: Document | None = None):
        doc = doc or self.doc
        cache: dict[str, object] = {}
        images = []
        for v in jobs:
            key = json.dumps(v, sort_keys=True, default=str)
            if key not in cache:
                cache[key] = head_image(doc, v, rotate180=self.cb_rot180.isChecked(),
                                        landscape_cw=self.cb_landcw.isChecked(),
                                        offset_mm=(self.sp_offx.value(), self.sp_offy.value()),
                                        threshold=self.sl_thr.value())
            images.append(cache[key])
        return images

    def print_now(self, *_):
        try:
            jobs = self.build_jobs()
        except ValueError as e:
            QMessageBox.information(self, "Drucken", str(e))
            return
        if not jobs:
            QMessageBox.information(self, "Drucken", "Nichts zu drucken (Anzahl 0).")
            return
        if len(jobs) > 30 and QMessageBox.question(
                self, "Drucken", f"{len(jobs)} Etiketten drucken?") != QMessageBox.Yes:
            return
        self._start_print(self._render_jobs(jobs))

    def _start_print(self, images):
        self.print_btn.setEnabled(False)
        self.btn_print2.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.job_label.setText("Starte Druck …")
        self.st_label.setText("Druckt …")
        self.request_print.emit(images, self.sl_density.value(), self.cb_method.currentData())

    def on_print_finished(self, ok: bool, msg: str):
        self.print_btn.setEnabled(True)
        self.btn_print2.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.job_label.setText(("✓ " if ok else "✗ ") + msg)
        if not ok:
            QMessageBox.warning(self, "Druckfehler", msg)
        else:
            self.statusBar().showMessage(msg, 5000)

    def test_print(self):
        d = Document(width_mm=self.doc.width_mm, length_mm=self.doc.length_mm, landscape=self.doc.landscape)
        W, H = d.design_w, d.design_h
        d.elements = [
            Element.new("rect", x=1, y=1, w=W - 2, h=H - 2, props={"line_mm": 0.25}),
            Element.new("text", x=2, y=2, w=W - 4, h=(H - 4) * 0.45,
                        props={"text": "▲ OBEN", "bold": True, "size_pt": 40}),
            Element.new("text", x=2, y=2 + (H - 4) * 0.5, w=W - 4, h=(H - 4) * 0.45,
                        props={"text": f"{d.width_mm:g} × {d.length_mm:g} mm\nlinks ◀ ▶ rechts", "size_pt": 12}),
        ]
        self._start_print(self._render_jobs([builtin_vars(1)], d))

    # ================================================================ files
    def maybe_save(self) -> bool:
        if not self.dirty or not self.path:
            return True
        r = QMessageBox.question(self, "Mutschiketten", "Änderungen speichern?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.file_save()
        return r == QMessageBox.Discard

    def file_new(self):
        if not self.maybe_save():
            return
        d = Document(width_mm=self.doc.width_mm, length_mm=self.doc.length_mm, density=self.doc.density)
        self.path = None
        self.load_document(d, reset_history=True)
        self.dirty = False
        self.update_title()
        self.view.fit()

    def file_open(self):
        if not self.maybe_save():
            return
        fn, _ = QFileDialog.getOpenFileName(self, "Etikett öffnen", self.settings.value("last_dir", ""), FILE_FILTER)
        if fn:
            self.open_path(fn)

    def open_path(self, fn: str):
        try:
            with open(fn, encoding="utf-8") as f:
                doc = Document.from_json(f.read())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Öffnen", f"Datei konnte nicht geöffnet werden:\n{e}")
            return
        self.path = fn
        self.settings.setValue("last_dir", os.path.dirname(fn))
        self.add_recent(fn)
        self.load_document(doc, reset_history=True)
        self.dirty = False
        self.update_title()
        self.view.fit()

    def file_save(self) -> bool:
        if not self.path:
            return self.file_save_as()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(self.doc.to_json())
        except OSError as e:
            QMessageBox.warning(self, "Speichern", str(e))
            return False
        self.dirty = False
        self.add_recent(self.path)
        self.update_title()
        self.statusBar().showMessage("Gespeichert", 2500)
        return True

    def file_save_as(self) -> bool:
        start = self.path or os.path.join(self.settings.value("last_dir", ""), "Etikett.etikett")
        fn, _ = QFileDialog.getSaveFileName(self, "Speichern unter", start, FILE_FILTER)
        if not fn:
            return False
        if not fn.lower().endswith(".etikett"):
            fn += ".etikett"
        self.path = fn
        self.settings.setValue("last_dir", os.path.dirname(fn))
        return self.file_save()

    def export_png(self):
        fn, _ = QFileDialog.getSaveFileName(self, "PNG exportieren", self.settings.value("last_dir", ""), "PNG (*.png)")
        if not fn:
            return
        import numpy as np
        arr = head_image(self.doc, self.scene.values, threshold=self.sl_thr.value())
        if self.doc.landscape:
            arr = np.rot90(arr, k=1 if self.cb_landcw.isChecked() else -1)
        bool_to_qimage(arr).save(fn if fn.lower().endswith(".png") else fn + ".png")

    def add_recent(self, fn: str):
        rec = [r for r in (self.settings.value("recent", []) or []) if r != fn]
        if isinstance(rec, str):
            rec = [rec]
        self.settings.setValue("recent", [fn] + rec[:9])

    def fill_recent(self):
        self.recent_menu.clear()
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        for r in rec:
            if os.path.exists(r):
                self.recent_menu.addAction(os.path.basename(r), lambda r=r: self.maybe_save() and self.open_path(r))
        if self.recent_menu.isEmpty():
            a = self.recent_menu.addAction("(leer)")
            a.setEnabled(False)

    # ================================================================ view toggles
    def toggle_grid(self):
        self.scene.show_grid = self.a_grid.isChecked()
        self.scene.update()

    def toggle_snap(self):
        self.scene.snap_enabled = self.a_snap.isChecked()

    def toggle_frames(self):
        self.scene.show_frames = self.a_frames.isChecked()
        self.scene.update()

    def show_help(self):
        QMessageBox.information(self, "Tastenkürzel & Tipps", (
            "<b>Bearbeiten</b><br>"
            "Pfeiltasten: 0,5 mm verschieben · Umschalt: 2 mm · Alt: 1 Druckpunkt (0,125 mm)<br>"
            "Strg+D duplizieren · Entf löschen · Strg+C / Strg+V kopieren/einfügen<br>"
            "Strg+Z / Strg+Y rückgängig / wiederholen<br>"
            "Ecke unten rechts ziehen = Größe ändern (Umschalt: Seitenverhältnis halten)<br><br>"
            "<b>Ansicht</b><br>Mausrad = Zoom · Strg+0 einpassen · Strg+B 1-Bit-Vorschau<br><br>"
            "<b>Platzhalter</b><br>{datum} {zeit} {kw} {nr} {nr:3} und jede CSV-Spalte als {Spalte}.<br>"
            "Bilder per Drag & Drop oder Strg+V aus der Zwischenablage einfügen.<br><br>"
            "<b>Kalibrierung</b><br>Mit „Testdruck“ prüfen, ob ▲ OBEN richtig herum herauskommt; "
            "sonst „Ausgabe um 180° drehen“ aktivieren."))

    def show_about(self):
        QMessageBox.about(self, "Über Mutschiketten", (
            f"<b>Mutschiketten {__version__}</b><br>Etiketten-Designer für Katasymbol / SUPVAN T50M Pro.<br>"
            "© 2026 Jürgen Mutscheller · <a href='https://mutschweb.de'>mutschweb.de</a> · MIT-Lizenz<br><br>"
            "Druckerprotokoll nach den MIT-lizenzierten Projekten heeen/supvan-cups, "
            "zpan45/t50-windows-driver und KamilBakDev/katasymbol-t50m-print.<br>"
            "Nicht mit SUPVAN oder Katasymbol verbunden."))

    def closeEvent(self, e):
        if self.path and self.dirty:
            if not self.maybe_save():
                e.ignore()
                return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("last_document", self.doc.to_json())
        self.settings.setValue("last_path", self.path or "")
        self.worker.cancel.set()
        from PySide6.QtCore import QMetaObject
        QMetaObject.invokeMethod(self.worker, "stop", Qt.BlockingQueuedConnection)
        self.thread.quit()
        self.thread.wait(2000)
        super().closeEvent(e)
