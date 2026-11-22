"""Editor canvas: scene, view and graphics items for label elements."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
                               QStyleOptionGraphicsItem)

from .model import DPMM, Document, Element, snap_mm
from .render import draw_element
from .protocol import HEAD_DOTS, MARGIN_DOTS

SNAP_PX = 7          # snap distance in screen pixels
HANDLE_PX = 9        # resize handle size in screen pixels
ACCENT = QColor(37, 99, 235)
GUIDE = QColor(236, 72, 153)


class ElementItem(QGraphicsItem):
    def __init__(self, el: Element, scene: "LabelScene"):
        super().__init__()
        self.el = el
        self._scene = scene
        self._resizing = False
        self._press_geom = None
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemIsFocusable)
        self.setAcceptHoverEvents(True)
        self.sync_from_model()

    # ------------------------------------------------------------ geometry
    def sync_from_model(self) -> None:
        self.prepareGeometryChange()
        self.setFlag(QGraphicsItem.ItemIsMovable, not self.el.locked)
        self._sync = True
        self.setPos(self.el.x * DPMM, self.el.y * DPMM)
        self._sync = False
        self.setTransformOriginPoint(self.el.w * DPMM / 2, self.el.h * DPMM / 2)
        self.setRotation(self.el.rotation)
        self.update()

    def _rect(self) -> QRectF:
        return QRectF(0, 0, self.el.w * DPMM, self.el.h * DPMM)

    def _scale(self) -> float:
        v = self.scene().views() if self.scene() else []
        return v[0].transform().m11() if v else 1.0

    def boundingRect(self) -> QRectF:
        m = HANDLE_PX / max(self._scale(), 0.01)
        return self._rect().adjusted(-m, -m, m, m)

    def _handle_rect(self) -> QRectF:
        s = HANDLE_PX / max(self._scale(), 0.01)
        r = self._rect()
        return QRectF(r.right() - s / 2, r.bottom() - s / 2, s, s)

    # ------------------------------------------------------------ painting
    def paint(self, p: QPainter, opt: QStyleOptionGraphicsItem, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        draw_element(p, self.el, self._scene.values)
        sc = max(self._scale(), 0.01)
        if self.isSelected():
            pen = QPen(ACCENT, 1.5 / sc, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(self._rect())
            if not self.el.locked:
                p.setPen(QPen(Qt.white, 1 / sc))
                p.setBrush(ACCENT)
                p.drawRect(self._handle_rect())
        elif self._scene.show_frames:
            p.setPen(QPen(QColor(150, 150, 150, 120), 1 / sc, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self._rect())

    # ------------------------------------------------------------ interaction
    def hoverMoveEvent(self, e):
        if self.isSelected() and not self.el.locked and self._handle_rect().contains(e.pos()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.SizeAllCursor if not self.el.locked else Qt.ArrowCursor)
        super().hoverMoveEvent(e)

    def mousePressEvent(self, e):
        self._press_geom = (self.el.x, self.el.y, self.el.w, self.el.h)
        if (e.button() == Qt.LeftButton and self.isSelected() and not self.el.locked
                and self._handle_rect().contains(e.pos())):
            self._resizing = True
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing:
            p = e.pos()
            w = max(1.0, snap_mm(p.x() / DPMM))
            h = max(1.0, snap_mm(p.y() / DPMM))
            if self.el.type in ("qr",) or (e.modifiers() & Qt.ShiftModifier):
                x0, y0, w0, h0 = self._press_geom
                ratio = h0 / w0 if w0 else 1
                h = snap_mm(w * ratio)
            w, h = self._scene.snap_size(self, w, h)
            self.prepareGeometryChange()
            self.el.w, self.el.h = w, h
            self.setTransformOriginPoint(w * DPMM / 2, h * DPMM / 2)
            self.update()
            self._scene.element_live.emit(self.el)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        was = self._resizing
        self._resizing = False
        super().mouseReleaseEvent(e)
        self._scene.guides = []
        self._scene.update()
        if self._press_geom != (self.el.x, self.el.y, self.el.w, self.el.h) or was:
            self._scene.element_changed.emit(self.el)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and not getattr(self, "_sync", False):
            pos: QPointF = value
            x, y = self._scene.snap_pos(self, pos.x() / DPMM, pos.y() / DPMM)
            return QPointF(x * DPMM, y * DPMM)
        if change == QGraphicsItem.ItemPositionHasChanged and not getattr(self, "_sync", False):
            self.el.x = snap_mm(self.pos().x() / DPMM)
            self.el.y = snap_mm(self.pos().y() / DPMM)
            self._scene.element_live.emit(self.el)
        return super().itemChange(change, value)


class LabelScene(QGraphicsScene):
    element_changed = Signal(object)   # committed change (undo point)
    element_live = Signal(object)      # live update while dragging

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = Document()
        self.values: dict = {}
        self.items_by_id: dict[str, ElementItem] = {}
        self.guides: list[tuple[str, float]] = []
        self.snap_enabled = True
        self.grid_mm = 1.0
        self.show_grid = False
        self.show_frames = True
        self.preview_item: QGraphicsPixmapItem | None = None
        self.setBackgroundBrush(QColor(228, 230, 235))

    # ------------------------------------------------------------ building
    def set_document(self, doc: Document) -> None:
        sel = {i.el.id for i in self.selectedItems() if isinstance(i, ElementItem)}
        self.doc = doc
        for it in list(self.items_by_id.values()):
            self.removeItem(it)
        self.items_by_id.clear()
        for z, el in enumerate(doc.elements):
            it = ElementItem(el, self)
            it.setZValue(z)
            self.addItem(it)
            self.items_by_id[el.id] = it
            if el.id in sel:
                it.setSelected(True)
        self.update_rect()

    def update_rect(self) -> None:
        W, H = self.doc.design_w * DPMM, self.doc.design_h * DPMM
        pad = max(W, H) * 0.25 + 40
        self.setSceneRect(QRectF(-pad, -pad, W + 2 * pad, H + 2 * pad))
        self.update()

    def selected_elements(self) -> list[Element]:
        return [i.el for i in self.selectedItems() if isinstance(i, ElementItem)]

    def set_preview(self, pix: QPixmap | None) -> None:
        if self.preview_item is not None:
            self.removeItem(self.preview_item)
            self.preview_item = None
        for it in self.items_by_id.values():
            it.setVisible(pix is None)
        if pix is not None:
            self.preview_item = QGraphicsPixmapItem(pix)
            self.preview_item.setTransformationMode(Qt.FastTransformation)
            self.preview_item.setZValue(10_000)
            self.addItem(self.preview_item)

    # ------------------------------------------------------------ snapping
    def _targets(self, exclude: ElementItem, axis: str) -> list[float]:
        L = self.doc.design_w if axis == "x" else self.doc.design_h
        t = [0.0, L / 2, L]
        for it in self.items_by_id.values():
            if it is exclude or not it.isVisible():
                continue
            e = it.el
            a, s = (e.x, e.w) if axis == "x" else (e.y, e.h)
            t += [a, a + s / 2, a + s]
        return t

    def _tol(self) -> float:
        v = self.views()
        sc = v[0].transform().m11() if v else 1.0
        return SNAP_PX / max(sc, 0.01) / DPMM

    def snap_pos(self, item: ElementItem, x: float, y: float) -> tuple[float, float]:
        x, y = snap_mm(x), snap_mm(y)
        self.guides = []
        if not self.snap_enabled:
            return x, y
        tol = self._tol()
        e = item.el
        out = []
        for axis, v, size in (("x", x, e.w), ("y", y, e.h)):
            best = None
            for t in self._targets(item, axis):
                for off in (0.0, size / 2, size):
                    d = t - (v + off)
                    if abs(d) <= tol and (best is None or abs(d) < abs(best[0])):
                        best = (d, t)
            if best is not None:
                v = snap_mm(v + best[0])
                self.guides.append((axis, best[1]))
            elif self.show_grid and self.grid_mm > 0:
                g = self.grid_mm
                v2 = round(v / g) * g
                if abs(v2 - v) <= tol:
                    v = snap_mm(v2)
            out.append(v)
        self.update()
        return out[0], out[1]

    def snap_size(self, item: ElementItem, w: float, h: float) -> tuple[float, float]:
        self.guides = []
        if not self.snap_enabled:
            return w, h
        tol = self._tol()
        e = item.el
        res = []
        for axis, a, s in (("x", e.x, w), ("y", e.y, h)):
            best = None
            for t in self._targets(item, axis):
                d = t - (a + s)
                if abs(d) <= tol and (best is None or abs(d) < abs(best[0])):
                    best = (d, t)
            if best is not None:
                s = snap_mm(s + best[0])
                self.guides.append((axis, best[1]))
            res.append(max(1.0, s))
        self.update()
        return res[0], res[1]

    # ------------------------------------------------------------ backdrop
    def drawBackground(self, p: QPainter, rect: QRectF) -> None:
        super().drawBackground(p, rect)
        doc = self.doc
        W, H = doc.design_w * DPMM, doc.design_h * DPMM
        lab = QRectF(0, 0, W, H)
        p.setRenderHint(QPainter.Antialiasing, True)
        # shadow + label
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 35))
        p.drawRoundedRect(lab.translated(3, 4), 10, 10)
        p.setBrush(Qt.white)
        p.drawRoundedRect(lab, 10, 10)
        # non printable zones: first/last MARGIN_DOTS along the feed, beyond head width across
        hatch = QBrush(QColor(239, 68, 68, 45), Qt.BDiagPattern)
        zones = []
        m = MARGIN_DOTS
        if doc.landscape:   # feed runs along X
            zones += [QRectF(0, 0, m, H), QRectF(W - m, 0, m, H)]
            over = H - HEAD_DOTS
            if over > 0:
                zones += [QRectF(0, 0, W, over / 2), QRectF(0, H - over / 2, W, over / 2)]
        else:
            zones += [QRectF(0, 0, W, m), QRectF(0, H - m, W, m)]
            over = W - HEAD_DOTS
            if over > 0:
                zones += [QRectF(0, 0, over / 2, H), QRectF(W - over / 2, 0, over / 2, H)]
        for z in zones:
            p.fillRect(z, hatch)
        if self.show_grid and self.grid_mm > 0:
            g = self.grid_mm * DPMM
            p.setPen(QPen(QColor(0, 0, 0, 28), 0))
            x = g
            while x < W:
                p.drawLine(QPointF(x, 0), QPointF(x, H))
                x += g
            y = g
            while y < H:
                p.drawLine(QPointF(0, y), QPointF(W, y))
                y += g
        # feed direction hint
        p.setPen(QPen(QColor(120, 120, 130), 0))
        f = p.font()
        f.setPixelSize(int(max(10, min(W, H) * 0.06)))
        p.setFont(f)
        if doc.landscape:
            p.drawText(QRectF(0, H + 6, W, 40), Qt.AlignHCenter | Qt.AlignTop, "◀ Papiervorschub")
        else:
            p.drawText(QRectF(W + 8, 0, 200, H), Qt.AlignLeft | Qt.AlignVCenter, "▲\nPapier-\nvorschub")

    def drawForeground(self, p: QPainter, rect: QRectF) -> None:
        W, H = self.doc.design_w * DPMM, self.doc.design_h * DPMM
        p.setPen(QPen(QColor(160, 165, 175), 0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(0, 0, W, H), 10, 10)
        if self.guides:
            pen = QPen(GUIDE, 0)
            p.setPen(pen)
            for axis, v in self.guides:
                if axis == "x":
                    p.drawLine(QPointF(v * DPMM, -2000), QPointF(v * DPMM, H + 2000))
                else:
                    p.drawLine(QPointF(-2000, v * DPMM), QPointF(W + 2000, v * DPMM))


class LabelView(QGraphicsView):
    zoom_changed = Signal(float)
    files_dropped = Signal(list)
    arrow_pressed = Signal(int, int, object)

    _ARROWS = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}

    def __init__(self, scene: LabelScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setAcceptDrops(True)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._pan = None

    # middle mouse button (or Space+drag) pans the view
    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan = e.position()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None and e.button() == Qt.MiddleButton:
            self._pan = None
            self.unsetCursor()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def fit(self) -> None:
        sc: LabelScene = self.scene()
        W, H = sc.doc.design_w * DPMM, sc.doc.design_h * DPMM
        self.resetTransform()
        self.fitInView(QRectF(-30, -30, W + 140, H + 80), Qt.KeepAspectRatio)
        self.zoom_changed.emit(self.transform().m11())

    def zoom(self, factor: float) -> None:
        cur = self.transform().m11()
        factor = max(0.2 / cur, min(factor, 30 / cur))
        self.scale(factor, factor)
        self.zoom_changed.emit(self.transform().m11())

    def wheelEvent(self, e):
        # labels are small: the wheel always zooms
        self.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)

    def keyPressEvent(self, e):
        d = self._ARROWS.get(e.key())
        if d and self.scene().selectedItems():
            self.arrow_pressed.emit(d[0], d[1], e.modifiers())
            e.accept()
            return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.files_dropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
            e.acceptProposedAction()
        else:
            super().dropEvent(e)
