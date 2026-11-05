"""The Geomorph Shipyard window."""

from __future__ import annotations

import datetime
import math
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelectionModel,
    QModelIndex,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import model
from .images import HighResStore, ImageStore
from .model import Deck, Part, PartInfo, Ship

DATA_DIR = Path(__file__).parent / "data" / "geomorphs"
APP_NAME = "Geomorph Shipyard"

OUTLINE_Z = 1_000_000
MIN_SCALE = 2**-3
MAX_SCALE = 2**3
START_SCALE = 2**1.5

# Low-res tiles hold 12 pixels per map unit and high-res tiles hold 60.
EXPORT_PIXELS_PER_UNIT = 12
EXPORT_HIGH_RES_PIXELS_PER_UNIT = 30
EXPORT_MAX_SIDE_PIXELS = 16_000
EXPORT_MAX_TOTAL_PIXELS = 150_000_000
EXPORT_MARGIN_UNITS = 10
EXPORT_LABEL_PIXELS = 34
EXPORT_CREDIT_PIXELS = 28
TRACE_OPACITY = 0.35
TRACE_Z = -1
ORIENTATION_SIZE = 196
ORIENTATION_COMPACT_SIZE = 104
ORIENTATION_MARGIN = 10
ORIENTATION_BUTTON_SIZE = 26
ORIENTATION_BUTTON_INSET = 6
SIDE_LABELS = {
    "Bow": "Front",
    "Stern": "Back",
    "Port": "Left",
    "Starboard": "Right",
}

EXPORT_CREDIT = (
    "Geomorphs by Robert Pearce and Eric B. Smith, CC BY-NC 4.0. "
    "Traveller is a trademark of Mongoose Publishing."
)


class PartItem(QGraphicsItem):
    """A placed part. Its position is the part's south-west corner.

    Inside the item y grows downwards, as it does on screen, so a map offset
    of (dx, dy) sits at (dx, -dy).
    """

    def __init__(self, part: Part, info: PartInfo, images: ImageStore, moved):
        super().__init__()
        self.part = part
        self.info = info
        self.images = images
        self._moved = moved
        self._syncing = False
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.sync()

    def sync(self) -> None:
        """Bring the item back in line with its part."""
        self.prepareGeometryChange()
        self._placement = model.place(self.part, self.info)
        self._syncing = True
        self.setPos(self.part.corner[0], -self.part.corner[1])
        self._syncing = False
        self.update()

    def _local(self, point: tuple[int, int]) -> QPointF:
        return QPointF(
            point[0] - self.part.corner[0], self.part.corner[1] - point[1]
        )

    def part_rect(self) -> QRectF:
        """The part itself, without the image's border."""
        west, south, east, north = self._placement.bounds
        return QRectF(self._local((west, north)), self._local((east, south)))

    def boundingRect(self) -> QRectF:
        corners = [
            self._local(self._placement.top_left),
            self._local(self._placement.top_right),
            self._local(self._placement.bottom_left),
        ]
        xs = [corner.x() for corner in corners]
        ys = [corner.y() for corner in corners]
        # The image is a rectangle, so three corners give its whole extent
        # once the corner opposite the top left is added.
        xs.append(xs[1] + xs[2] - xs[0])
        ys.append(ys[1] + ys[2] - ys[0])
        rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        return rect.adjusted(-1, -1, 1, 1)

    def shape(self) -> QPainterPath:
        # Only the part itself is clickable, not the image's empty border.
        path = QPainterPath()
        path.addRect(self.part_rect())
        return path

    def _image_transform(self, width: int, height: int) -> QTransform:
        top_left = self._local(self._placement.top_left)
        top_right = self._local(self._placement.top_right)
        bottom_left = self._local(self._placement.bottom_left)
        return QTransform(
            (top_right.x() - top_left.x()) / width,
            (top_right.y() - top_left.y()) / width,
            (bottom_left.x() - top_left.x()) / height,
            (bottom_left.y() - top_left.y()) / height,
            top_left.x(),
            top_left.y(),
        )

    def _draw_image(self, painter: QPainter, pixmap) -> None:
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setTransform(
            self._image_transform(pixmap.width(), pixmap.height()), True
        )
        painter.drawPixmap(0, 0, pixmap)
        painter.restore()

    def _draw_placeholder(self, painter: QPainter) -> None:
        rect = self.part_rect()
        painter.save()
        painter.setPen(QPen(QColor("#5b6b7a"), 0))
        painter.setBrush(QColor(214, 225, 236, 235))
        painter.drawRect(rect)

        # A dot near the image's top edge shows which way the part is turned.
        top_left = self._local(self._placement.top_left)
        top_right = self._local(self._placement.top_right)
        centre = rect.center()
        edge = (top_left + top_right) / 2
        distance = math.hypot(edge.x() - centre.x(), edge.y() - centre.y())
        dot = centre + (edge - centre) * ((distance - 13) / distance)
        painter.setBrush(QColor("#5b6b7a"))
        painter.drawEllipse(dot, 1.2, 1.2)

        font = QFont()
        font.setPointSizeF(2.6)
        painter.setFont(font)
        painter.setPen(QColor("#22303c"))
        label = self.part.code + (" (flipped)" if self.part.mirror else "")
        painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, label)
        painter.restore()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        mirror = self.part.mirror
        base = self.images.pixmap(self.info.image(mirror))
        if base is None:
            self._draw_placeholder(painter)
        else:
            self._draw_image(painter, base)

        if self.part.overlay:
            overlay = self.images.pixmap(self.info.overlay_image(mirror))
            if overlay is not None:
                self._draw_image(painter, overlay)

    @staticmethod
    def _treat_shift_as_multi_select(event) -> None:
        # Qt adds to a selection with Ctrl (Cmd on a Mac), and also handles
        # dragging a whole selection, so Shift is passed off as that.
        if event.modifiers() & Qt.ShiftModifier:
            event.setModifiers(event.modifiers() | Qt.ControlModifier)

    def mousePressEvent(self, event) -> None:
        self._treat_shift_as_multi_select(event)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._treat_shift_as_multi_select(event)
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and not self._syncing:
            return QPointF(model.snap(value.x()), model.snap(value.y()))
        if change == QGraphicsItem.ItemPositionHasChanged and not self._syncing:
            corner = (int(self.pos().x()), int(-self.pos().y()))
            if corner != tuple(self.part.corner):
                self.part.corner = corner
                # The drawing is worked out from this, so it has to move too.
                self._placement = model.place(self.part, self.info)
                self._moved(self)
        return super().itemChange(change, value)


class TraceItem(PartItem):
    """A part from another deck, shown faintly and out of reach of the mouse."""

    def __init__(self, part: Part, info: PartInfo, images: ImageStore):
        super().__init__(part, info, images, lambda item: None)
        self.setFlags(QGraphicsItem.GraphicsItemFlag(0))
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setOpacity(TRACE_OPACITY)
        self.setZValue(TRACE_Z)


class ShipScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(-20_000, -20_000, 40_000, 40_000, parent)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor("#f8f8f8"))
        scale = painter.worldTransform().m11()
        step = model.UNITS_PER_SQUARE if scale * model.UNITS_PER_SQUARE >= 6 else 50
        first_x = math.floor(rect.left() / step) * step
        first_y = math.floor(rect.top() / step) * step
        fine = QPen(QColor("#e2e6ea"), 0)
        strong = QPen(QColor("#c5ccd3"), 0)

        x = first_x
        while x <= rect.right():
            painter.setPen(strong if x % 50 == 0 else fine)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        y = first_y
        while y <= rect.bottom():
            painter.setPen(strong if y % 50 == 0 else fine)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step


class ShipView(QGraphicsView):
    """Drag empty space to pan, scroll to zoom."""

    arrow_pressed = Signal(int, int)

    def __init__(self, scene: QGraphicsScene):
        super().__init__(scene)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setFocusPolicy(Qt.StrongFocus)
        self._ignoring_click = False
        self.scale(START_SCALE, START_SCALE)
        self.centerOn(25, 0)

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 240
        factor = 2**steps
        current = self.transform().m11()
        factor = max(MIN_SCALE / current, min(MAX_SCALE / current, factor))
        self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event) -> None:
        # A Shift-click that just misses a part shouldn't lose the selection.
        self._ignoring_click = False
        if event.modifiers() & Qt.ShiftModifier:
            point = self.mapToScene(event.position().toPoint())
            if not any(type(item) is PartItem for item in self.scene().items(point)):
                self._ignoring_click = True
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._ignoring_click:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        # Qt clears the selection on release in this drag mode, not on press.
        if self._ignoring_click:
            self._ignoring_click = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        moves = {
            Qt.Key_Left: (-model.SNAP_UNITS, 0),
            Qt.Key_Right: (model.SNAP_UNITS, 0),
            Qt.Key_Up: (0, model.SNAP_UNITS),
            Qt.Key_Down: (0, -model.SNAP_UNITS),
        }
        if event.key() in moves and self.scene().selectedItems():
            self.arrow_pressed.emit(*moves[event.key()])
            event.accept()
            return
        super().keyPressEvent(event)

    def map_centre(self) -> tuple[int, int]:
        centre = self.mapToScene(self.viewport().rect().center())
        return model.snap(centre.x()), model.snap(-centre.y())


class OrientationOverlay(QWidget):
    """A compass on the map showing which way the ship faces.

    It sits on the view, not in the scene, so it stays in the corner when the
    map is panned or zoomed. Turning it changes the labels and not the ship.
    """

    turn_requested = Signal(int)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.bow = model.DEFAULT_BOW
        self.compact = False
        self.setToolTip("Which way the ship faces. Turning this leaves the ship as it is.")
        self.left_button = self._button("↺", "Turn the bow to the left", -1)
        self.right_button = self._button("↻", "Turn the bow to the right", 1)
        self.size_button = QToolButton(self)
        self.size_button.setFixedSize(
            ORIENTATION_BUTTON_SIZE, ORIENTATION_BUTTON_SIZE
        )
        self.size_button.move(ORIENTATION_BUTTON_INSET, ORIENTATION_BUTTON_INSET)
        self.size_button.clicked.connect(lambda: self.set_compact(not self.compact))
        self.move(ORIENTATION_MARGIN, ORIENTATION_MARGIN)
        self._apply_size()

    def _button(self, text: str, tip: str, turn: int) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setToolTip(tip)
        button.setFixedSize(ORIENTATION_BUTTON_SIZE, ORIENTATION_BUTTON_SIZE)
        button.clicked.connect(lambda: self.turn_requested.emit(turn))
        return button

    def _apply_size(self) -> None:
        size = ORIENTATION_COMPACT_SIZE if self.compact else ORIENTATION_SIZE
        self.setFixedSize(size, size)
        far = size - ORIENTATION_BUTTON_SIZE - ORIENTATION_BUTTON_INSET
        self.left_button.move(ORIENTATION_BUTTON_INSET, far)
        self.right_button.move(far, far)
        self.size_button.setText("□" if self.compact else "−")
        self.size_button.setToolTip(
            "Show the compass with its labels"
            if self.compact
            else "Shrink the compass"
        )

    def set_compact(self, compact: bool) -> None:
        self.compact = compact
        self._apply_size()
        self.update()

    def set_bow(self, bow: str) -> None:
        self.bow = bow
        self.update()

    def labels(self) -> dict[str, str]:
        """The text shown on each edge."""
        return {
            direction: f"{side} / {SIDE_LABELS[side]}"
            for direction, side in model.sides_for_bow(self.bow).items()
        }

    def _text_rect(self, direction: str, width: int, height: int) -> QRectF:
        size = float(self.width())
        middle = size / 2
        spots = {
            "up": (middle - width / 2, 8),
            "down": (middle - width / 2, size - 8 - height),
            "left": (8, middle - height / 2),
            "right": (size - 8 - width, middle - height / 2),
        }
        x, y = spots[direction]
        return QRectF(x, y, width, height)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor("#9aa7b4")))
        painter.setBrush(QColor(255, 255, 255, 225))
        painter.drawRoundedRect(box, 10, 10)

        bold = QFont()
        bold.setPixelSize(11)
        bold.setBold(True)
        small = QFont()
        small.setPixelSize(10)
        sides = {} if self.compact else model.sides_for_bow(self.bow)
        for direction, side in sides.items():
            colour = QColor("#b3261e") if side == "Bow" else QColor("#22303c")
            width = max(
                QFontMetrics(bold).horizontalAdvance(side),
                QFontMetrics(small).horizontalAdvance(SIDE_LABELS[side]),
            ) + 2
            rect = self._text_rect(direction, width, 28)
            painter.setPen(colour)
            painter.setFont(bold)
            painter.drawText(rect, Qt.AlignTop | Qt.AlignHCenter, side)
            painter.setFont(small)
            painter.drawText(
                rect, Qt.AlignBottom | Qt.AlignHCenter, SIDE_LABELS[side]
            )

        # A small hull shape pointing at the bow.
        turn = model.BOW_DIRECTIONS.index(self.bow) * 90
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(turn)
        painter.setPen(QPen(QColor("#22303c"), 1.2))
        painter.setBrush(QColor("#dbe3ea"))
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(0, -24),
                    QPointF(11, -6),
                    QPointF(11, 22),
                    QPointF(-11, 22),
                    QPointF(-11, -6),
                ]
            )
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#b3261e"))
        painter.drawPolygon(
            QPolygonF([QPointF(0, -24), QPointF(11, -6), QPointF(-11, -6)])
        )


class LibraryModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.parts: list[PartInfo] = []

    def set_parts(self, parts: list[PartInfo]) -> None:
        self.beginResetModel()
        self.parts = parts
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.parts)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        info = self.parts[index.row()]
        if role == Qt.DisplayRole:
            return f"{info.code}\n{info.summary()}"
        if role == Qt.ToolTipRole:
            return f"{info.code}: {info.summary()}"
        if role == Qt.UserRole:
            return info
        return None


def checklist(entries: list[tuple[str, str]], changed) -> QListWidget:
    """A list of ticks. Each entry is (key, label)."""
    widget = QListWidget()
    widget.setMaximumHeight(130)
    for key, label in entries:
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, key)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Unchecked)
        widget.addItem(item)
    widget.itemChanged.connect(changed)
    return widget


def ticked(widget: QListWidget) -> set[str]:
    return {
        widget.item(row).data(Qt.UserRole)
        for row in range(widget.count())
        if widget.item(row).checkState() == Qt.Checked
    }


def table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


class MainWindow(QMainWindow):
    def __init__(
        self,
        library: dict[str, PartInfo],
        images: ImageStore | None = None,
        high_res: HighResStore | None = None,
    ):
        super().__init__()
        self.library = library
        self.images = ImageStore() if images is None else images
        self.high_res = high_res or HighResStore(self.images)
        self.ship = Ship.empty()
        self.current = 0
        self.trace_deck: Deck | None = None
        self.path: Path | None = None
        self.dirty = False
        self.clipboard: list[Part] = []
        self.items: list[PartItem] = []
        self.trace_items: list[TraceItem] = []

        self.scene = ShipScene(self)
        self.view = ShipView(self.scene)
        self.orientation = OrientationOverlay(self.view)
        self.orientation.set_bow(self.ship.bow)
        self.orientation.turn_requested.connect(self.turn_bow)

        self.outline = QGraphicsPathItem()
        self.outline.setPen(self._outline_pen())
        self.scene.addItem(self.outline)
        self.outline.setZValue(OUTLINE_Z)
        self.outline.setAcceptedMouseButtons(Qt.NoButton)
        self.outline.hide()

        self._build_actions()
        self._build_toolbar()
        self.setCentralWidget(self._build_map_area())
        self._build_side_panel()

        # Dragging a group moves every part on every mouse step, so the
        # tables wait until the event loop is free, and refresh once.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._refresh_all)

        self.scene.selectionChanged.connect(self._selection_changed)
        self.view.arrow_pressed.connect(self.move_selected)

        self.resize(1320, 860)
        self._refresh_decks()
        self._refresh_all()
        self._refresh_library()
        if not self.images.has_any_part_images(library):
            self.statusBar().showMessage(
                "No geomorph images found, so parts show as labelled "
                "placeholders. See the README to fetch the images."
            )

    @staticmethod
    def _outline_pen() -> QPen:
        pen = QPen(QColor("#2a6fff"))
        pen.setCosmetic(True)
        pen.setWidth(3)
        return pen

    # Setting up

    def _action(self, text, handler, shortcuts=()) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda checked=False: handler())
        if shortcuts:
            action.setShortcuts(list(shortcuts))
        self.addAction(action)
        return action

    def _build_actions(self) -> None:
        a = self._action
        self.new_action = a("New ship", self.new_ship, [QKeySequence.New])
        self.open_action = a("Open...", self.open_ship, [QKeySequence.Open])
        self.save_action = a("Save", self.save_ship, [QKeySequence.Save])
        self.save_as_action = a(
            "Save as...", self.save_ship_as, [QKeySequence.SaveAs]
        )
        self.export_action = a(
            "Export this deck...", self.export_image, ["Ctrl+E"]
        )
        self.export_all_action = a(
            "Export all decks...", self.export_all_decks, ["Ctrl+Shift+E"]
        )

        self.turn_bow_left_action = a(
            "Turn bow left", lambda: self.turn_bow(-1), ["["]
        )
        self.turn_bow_right_action = a(
            "Turn bow right", lambda: self.turn_bow(1), ["]"]
        )
        self.show_orientation_action = QAction("Show orientation", self)
        self.show_orientation_action.setCheckable(True)
        self.show_orientation_action.setChecked(True)
        self.show_orientation_action.toggled.connect(self._orientation_toggled)

        self.high_res_action = QAction("Export in high resolution", self)
        self.high_res_action.setCheckable(True)
        self.high_res_action.setEnabled(self.high_res.available())
        self.high_res_action.setChecked(self.high_res.available())

        self.add_deck_action = a("Add deck", self.add_deck, ["Ctrl+Shift+D"])
        self.duplicate_deck_action = a("Duplicate deck", self.duplicate_deck)
        self.rename_deck_action = a(
            "Rename deck...", lambda: self.rename_deck_prompt(self.current)
        )
        self.delete_deck_action = a("Delete deck", self.delete_deck)
        self.previous_deck_action = a(
            "Previous deck", lambda: self.step_deck(-1), [Qt.Key_PageUp]
        )
        self.next_deck_action = a(
            "Next deck", lambda: self.step_deck(1), [Qt.Key_PageDown]
        )

        self.ccw_action = a(
            "Rotate left", self.rotate_ccw, ["Shift+R"]
        )
        self.cw_action = a("Rotate right", self.rotate_cw, ["R"])
        self.clone_action = a("Clone", self.clone_selected, ["C"])
        self.delete_action = a(
            "Delete", self.delete_selected, [Qt.Key_Delete, Qt.Key_Backspace]
        )
        self.flip_action = a("Flip", self.toggle_mirror, ["S"])
        self.overlay_action = a(
            "Show cargo", self.toggle_overlay, ["O"]
        )
        self.deselect_action = a("Deselect", self.deselect, [Qt.Key_Escape])
        self.select_all_action = a(
            "Select all", self.select_all, [QKeySequence.SelectAll]
        )
        self.cut_action = a("Cut", self.cut_selected, [QKeySequence.Cut])
        self.copy_action = a("Copy", self.copy_selected, [QKeySequence.Copy])
        self.paste_action = a("Paste", self.paste, [QKeySequence.Paste])

        self.selection_actions = [
            self.clone_action,
            self.delete_action,
            self.deselect_action,
            self.cut_action,
            self.copy_action,
        ]

        file_menu = self.menuBar().addMenu("File")
        for action in (
            self.new_action,
            self.open_action,
            self.save_action,
            self.save_as_action,
            self.export_action,
            self.export_all_action,
            self.high_res_action,
        ):
            file_menu.addAction(action)

        edit_menu = self.menuBar().addMenu("Edit")
        for action in (
            self.cut_action,
            self.copy_action,
            self.paste_action,
            self.select_all_action,
        ):
            edit_menu.addAction(action)

        part_menu = self.menuBar().addMenu("Part")
        for action in (
            self.ccw_action,
            self.cw_action,
            self.flip_action,
            self.overlay_action,
            self.clone_action,
            self.delete_action,
            self.deselect_action,
        ):
            part_menu.addAction(action)
        self.move_menu = part_menu.addMenu("Move to deck")
        self.move_menu.aboutToShow.connect(self._fill_move_menu)

        ship_menu = self.menuBar().addMenu("Ship")
        ship_menu.addAction(self.turn_bow_left_action)
        ship_menu.addAction(self.turn_bow_right_action)
        ship_menu.addAction(self.show_orientation_action)

        deck_menu = self.menuBar().addMenu("Deck")
        for action in (
            self.add_deck_action,
            self.duplicate_deck_action,
            self.rename_deck_action,
            self.delete_deck_action,
            self.previous_deck_action,
            self.next_deck_action,
        ):
            deck_menu.addAction(action)

    def _build_toolbar(self) -> None:
        bar = QToolBar("Ship")
        bar.setMovable(False)
        self.addToolBar(bar)
        bar.addWidget(QLabel(" Ship name: "))
        self.name_edit = QLineEdit(self.ship.name)
        self.name_edit.setMaximumWidth(260)
        self.name_edit.textEdited.connect(self._name_edited)
        bar.addWidget(self.name_edit)
        bar.addSeparator()
        for action in (
            self.ccw_action,
            self.cw_action,
            self.flip_action,
            self.overlay_action,
            self.clone_action,
            self.delete_action,
        ):
            bar.addAction(action)
        bar.addSeparator()
        for action in (self.copy_action, self.paste_action):
            bar.addAction(action)

    def _build_map_area(self) -> QWidget:
        self.deck_tabs = QTabBar()
        self.deck_tabs.setMovable(True)
        self.deck_tabs.setExpanding(False)
        self.deck_tabs.setToolTip("Drag to reorder, double-click to rename")
        self.deck_tabs.currentChanged.connect(self.switch_deck)
        self.deck_tabs.tabMoved.connect(self._deck_tab_moved)
        self.deck_tabs.tabBarDoubleClicked.connect(self.rename_deck_prompt)

        self.trace_combo = QComboBox()
        self.trace_combo.setToolTip("Show another deck faintly under this one")
        self.trace_combo.activated.connect(self._trace_chosen)

        row = QHBoxLayout()
        row.setContentsMargins(6, 4, 6, 0)
        row.addWidget(self.deck_tabs)
        for action in (self.add_deck_action, self.duplicate_deck_action):
            button = QToolButton()
            button.setDefaultAction(action)
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(QLabel("Trace over:"))
        row.addWidget(self.trace_combo)

        area = QWidget()
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(row)
        layout.addWidget(self.view, 1)
        return area

    def _build_side_panel(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._build_library_tab(), "Library")
        tabs.addTab(self._build_used_tab(), "Parts used")
        tabs.addTab(self._build_tonnage_tab(), "Tonnage")
        self.tabs = tabs

        dock = QDockWidget("Parts")
        dock.setTitleBarWidget(QWidget())
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        dock.setWidget(tabs)
        dock.setMinimumWidth(360)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    def _build_library_tab(self) -> QWidget:
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search part codes")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self._refresh_library())

        self.usage_list = checklist(
            [(usage, usage) for usage in model.USAGE_FILTERS],
            lambda _item: self._refresh_library(),
        )
        self.shape_list = checklist(
            [(shape, model.SHAPE_NAMES[shape]) for shape in model.SHAPES],
            lambda _item: self._refresh_library(),
        )

        filters = QGroupBox("Filters (tick none to show all)")
        filter_layout = QHBoxLayout(filters)
        filter_layout.setContentsMargins(6, 6, 6, 6)
        filter_layout.addWidget(self.usage_list)
        filter_layout.addWidget(self.shape_list)

        self.library_model = LibraryModel()
        self.library_view = QListView()
        self.library_view.setModel(self.library_model)
        self.library_view.setUniformItemSizes(True)
        self.library_view.setAlternatingRowColors(True)
        self.library_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.library_view.doubleClicked.connect(lambda _index: self.add_current())
        self.library_view.selectionModel().currentChanged.connect(
            lambda current, _previous: self._show_preview()
        )

        self.count_label = QLabel()

        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(lambda: self.add_current())
        self.replace_button = QPushButton("Replace selected")
        self.replace_button.clicked.connect(lambda: self.replace_with_current())
        buttons = QHBoxLayout()
        buttons.addWidget(self.count_label, 1)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.replace_button)

        self.preview = QLabel("Choose a part to preview it")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(220)
        self.preview.setStyleSheet("background: #f8f8f8; color: #667;")

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(self.search)
        layout.addWidget(filters)
        layout.addWidget(self.library_view, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.preview)
        return tab

    def _build_used_tab(self) -> QWidget:
        self.used_table = table(["Code", "Corner", "Turned", "Cargo", "Flipped"])
        self.used_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.used_table.itemSelectionChanged.connect(self._used_rows_chosen)
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(self.used_table)
        return tab

    def _build_tonnage_tab(self) -> QWidget:
        self.tonnage_table = table(["Usage", "dTon", "%"])
        self.tonnage_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.deck_table = table(["Deck", "dTon"])
        self.deck_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.deck_table.setMaximumHeight(170)
        self.fuel_note = QLabel(
            "Rough guide only. Jump-1 ships want about 11% fuel, then about "
            "10% more for each jump number."
        )
        self.fuel_note.setWordWrap(True)
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(self.tonnage_table, 1)
        layout.addWidget(QLabel("By deck"))
        layout.addWidget(self.deck_table)
        layout.addWidget(self.fuel_note)
        return tab

    # Keeping everything in step

    @property
    def deck(self) -> Deck:
        return self.ship.decks[self.current]

    def selected_items(self) -> list[PartItem]:
        """The selected parts, in the order they sit on the deck."""
        return [item for item in self.items if item.isSelected()]

    def selected_indices(self) -> list[int]:
        return [i for i, item in enumerate(self.items) if item.isSelected()]

    def selected_item(self) -> PartItem | None:
        """The selected part, if exactly one is selected."""
        chosen = self.selected_items()
        return chosen[0] if len(chosen) == 1 else None

    def selected_index(self) -> int | None:
        chosen = self.selected_indices()
        return chosen[0] if len(chosen) == 1 else None

    def _rebuild_items(self, select: tuple[int, ...] | list[int] = ()) -> None:
        self.scene.blockSignals(True)
        for item in self.items + self.trace_items:
            self.scene.removeItem(item)
        self.items = []
        self.trace_items = []
        for z, part in enumerate(self.deck.parts):
            item = PartItem(part, self.library[part.code], self.images, self._part_moved)
            item.setZValue(z)
            self.scene.addItem(item)
            self.items.append(item)
        if self.trace_deck is not None and self.trace_deck is not self.deck:
            for part in self.trace_deck.parts:
                item = TraceItem(part, self.library[part.code], self.images)
                self.scene.addItem(item)
                self.trace_items.append(item)
        for index in select:
            self.items[index].setSelected(True)
        self.scene.blockSignals(False)
        self._refresh_all()

    def _changed(self) -> None:
        self.dirty = True
        self._refresh_all()

    def _part_moved(self, item: PartItem) -> None:
        self.dirty = True
        self._refresh_selection()
        self._refresh_timer.start()

    def _selection_changed(self) -> None:
        self._refresh_selection()
        self._refresh_used_selection()

    def _refresh_all(self) -> None:
        self._refresh_title()
        self._refresh_selection()
        self._refresh_used()
        self._refresh_tonnage()

    def _refresh_title(self) -> None:
        name = self.ship.name
        title = APP_NAME if name == model.DEFAULT_SHIP_NAME else f"{name} - {APP_NAME}"
        self.setWindowTitle(title + (" *" if self.dirty else ""))

    def _refresh_selection(self) -> None:
        chosen = self.selected_items()
        item = self.selected_item()
        for action in self.selection_actions:
            action.setEnabled(bool(chosen))
        self.select_all_action.setEnabled(bool(self.items))
        self.ccw_action.setEnabled(item is not None)
        self.cw_action.setEnabled(item is not None)
        self.flip_action.setEnabled(item is not None and item.info.has_mirror)
        self.overlay_action.setEnabled(
            item is not None
            and item.info.overlay_image(item.part.mirror) is not None
        )
        self.paste_action.setEnabled(bool(self.clipboard))
        self.replace_button.setEnabled(
            item is not None and self._current_library_part() is not None
        )
        self.move_menu.setEnabled(bool(chosen) and len(self.ship.decks) > 1)

        outline = QPainterPath()
        for part_item in chosen:
            outline.addRect(part_item.part_rect().translated(part_item.pos()))
        self.outline.setPath(outline)
        self.outline.setVisible(bool(chosen))

    def _refresh_used(self) -> None:
        turned = {0: "", 90: "90° left", 180: "180°", 270: "90° right"}
        rows = self.deck.parts
        self.used_table.blockSignals(True)
        self.used_table.setRowCount(len(rows))
        for row, part in enumerate(rows):
            values = (
                part.code,
                f"{part.corner[0]}, {part.corner[1]}",
                turned[part.rotation],
                "yes" if part.overlay else "",
                "yes" if part.mirror else "",
            )
            for column, value in enumerate(values):
                self.used_table.setItem(row, column, QTableWidgetItem(value))
        self.used_table.blockSignals(False)
        self._refresh_used_selection()

    def _refresh_used_selection(self) -> None:
        selection = self.used_table.selectionModel()
        self.used_table.blockSignals(True)
        selection.blockSignals(True)
        self.used_table.clearSelection()
        for index in self.selected_indices():
            selection.select(
                self.used_table.model().index(index, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
        selection.blockSignals(False)
        self.used_table.blockSignals(False)
        self.used_table.viewport().update()

    def _used_rows_chosen(self) -> None:
        rows = self.used_table.selectionModel().selectedRows()
        self.select_indices([row.row() for row in rows])

    def _refresh_tonnage(self) -> None:
        rows = model.tonnage(self.ship.parts, self.library)
        total = model.total_tons(rows)
        self.tonnage_table.setRowCount(len(rows) + 1)
        for row, (use, tons) in enumerate(rows):
            self._tonnage_row(
                row, use, model.format_tons(tons), f"{model.percent(tons, total)}%"
            )
        self._tonnage_row(len(rows), "Total", model.format_tons(total), "")

        self.deck_table.setRowCount(len(self.ship.decks))
        for row, deck in enumerate(self.ship.decks):
            tons = model.total_tons(model.tonnage(deck.parts, self.library))
            self.deck_table.setItem(row, 0, QTableWidgetItem(deck.name))
            cell = QTableWidgetItem(model.format_tons(tons))
            cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.deck_table.setItem(row, 1, cell)

    def _tonnage_row(self, row: int, use: str, tons: str, share: str) -> None:
        for column, value in enumerate((use, tons, share)):
            cell = QTableWidgetItem(value)
            if column:
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.tonnage_table.setItem(row, column, cell)

    def _refresh_decks(self) -> None:
        tabs = self.deck_tabs
        tabs.blockSignals(True)
        while tabs.count():
            tabs.removeTab(0)
        for deck in self.ship.decks:
            tabs.addTab(deck.name)
        tabs.setCurrentIndex(self.current)
        tabs.blockSignals(False)

        combo = self.trace_combo
        combo.clear()
        combo.addItem("Nothing", None)
        for deck in self.ship.decks:
            if deck is not self.deck:
                combo.addItem(deck.name, deck)
                if deck is self.trace_deck:
                    combo.setCurrentIndex(combo.count() - 1)

        self.delete_deck_action.setEnabled(len(self.ship.decks) > 1)

    def _refresh_library(self) -> None:
        current = self._current_library_part()
        parts = model.filter_library(
            self.library,
            usages=ticked(self.usage_list),
            shapes=ticked(self.shape_list),
            text=self.search.text(),
        )
        self.library_model.set_parts(parts)
        self.count_label.setText(f"{len(parts)} of {len(self.library)} parts")
        if current in parts:
            row = parts.index(current)
            self.library_view.setCurrentIndex(self.library_model.index(row))
        self.library_view.scrollToTop()
        self.add_button.setEnabled(self._current_library_part() is not None)
        self._refresh_selection()

    def _current_library_part(self) -> PartInfo | None:
        index = self.library_view.currentIndex()
        return index.data(Qt.UserRole) if index.isValid() else None

    def _show_preview(self) -> None:
        info = self._current_library_part()
        self.add_button.setEnabled(info is not None)
        self._refresh_selection()
        if info is None:
            return
        pixmap = self.images.pixmap(info.url)
        if pixmap is None:
            self.preview.setText(f"{info.code}\n{info.summary()}\n\nNo image on disk")
            return
        size = self.preview.size() - QSize(8, 8)
        self.preview.setPixmap(
            pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    # What the user can do

    def select_indices(self, indices: list[int]) -> None:
        self.scene.blockSignals(True)
        self.scene.clearSelection()
        for index in indices:
            self.items[index].setSelected(True)
        self.scene.blockSignals(False)
        self._selection_changed()

    def select_index(self, index: int) -> None:
        self.select_indices([index])

    def select_all(self) -> None:
        self.select_indices(list(range(len(self.items))))

    def deselect(self) -> None:
        self.scene.clearSelection()

    def add_part(self, code: str) -> None:
        self._add_parts([Part(code, self.view.map_centre())])

    def _add_parts(self, parts: list[Part]) -> None:
        """Add parts to this deck and leave them selected."""
        first = len(self.deck.parts)
        self.deck.parts.extend(parts)
        self.dirty = True
        self._rebuild_items(select=range(first, len(self.deck.parts)))
        self.view.setFocus()

    def _add_copies(self, parts: list[Part]) -> None:
        self._add_parts(
            model.copies_centred_on(parts, self.library, self.view.map_centre())
        )

    def add_current(self) -> None:
        info = self._current_library_part()
        if info is not None:
            self.add_part(info.code)

    def replace_with_current(self) -> None:
        item = self.selected_item()
        info = self._current_library_part()
        if item is None or info is None:
            return
        item.part.code = info.code
        item.part.overlay = False
        item.info = info
        item.sync()
        self._changed()

    def _edit_selected(self, change) -> None:
        item = self.selected_item()
        if item is None:
            return
        change(item.part)
        item.sync()
        self._changed()

    def rotate_cw(self) -> None:
        self._edit_selected(Part.rotate_clockwise)

    def rotate_ccw(self) -> None:
        self._edit_selected(Part.rotate_counter_clockwise)

    def move_selected(self, dx: int, dy: int) -> None:
        for item in self.selected_items():
            item.part.move(dx, dy)
            item.sync()
        self._changed()

    def toggle_mirror(self) -> None:
        item = self.selected_item()
        if item is not None and item.info.has_mirror:
            self._edit_selected(lambda part: setattr(part, "mirror", not part.mirror))

    def toggle_overlay(self) -> None:
        item = self.selected_item()
        if item is not None and item.info.overlay_image(item.part.mirror):
            self._edit_selected(
                lambda part: setattr(part, "overlay", not part.overlay)
            )

    def clone_selected(self) -> None:
        chosen = self.selected_items()
        if chosen:
            self._add_copies([item.part for item in chosen])

    def delete_selected(self) -> None:
        chosen = self.selected_indices()
        if not chosen:
            return
        for index in reversed(chosen):
            del self.deck.parts[index]
        self.dirty = True
        self._rebuild_items()

    def copy_selected(self) -> None:
        chosen = self.selected_items()
        if chosen:
            self.clipboard = [replace(item.part) for item in chosen]
            self._refresh_selection()

    def cut_selected(self) -> None:
        self.copy_selected()
        self.delete_selected()

    def paste(self) -> None:
        if self.clipboard:
            self._add_copies(self.clipboard)

    def _fill_move_menu(self) -> None:
        self.move_menu.clear()
        for index, deck in enumerate(self.ship.decks):
            action = self.move_menu.addAction(deck.name)
            action.setEnabled(index != self.current)
            action.triggered.connect(
                lambda checked=False, i=index: self.move_selected_to_deck(i)
            )

    def move_selected_to_deck(self, index: int) -> None:
        chosen = self.selected_indices()
        if not chosen or index == self.current:
            return
        moving = [self.deck.parts[position] for position in chosen]
        for position in reversed(chosen):
            del self.deck.parts[position]
        self.ship.decks[index].parts.extend(moving)
        self.dirty = True
        self._rebuild_items()

    # Decks

    def _after_deck_change(self) -> None:
        self.dirty = True
        self._refresh_decks()
        self._rebuild_items()

    def switch_deck(self, index: int) -> None:
        if index < 0 or index == self.current:
            return
        self.current = index
        self._refresh_decks()
        self._rebuild_items()
        self.view.setFocus()

    def step_deck(self, step: int) -> None:
        self.switch_deck((self.current + step) % len(self.ship.decks))

    def add_deck(self) -> None:
        self.current += 1
        self.ship.add_deck(self.current)
        self._after_deck_change()

    def duplicate_deck(self) -> None:
        self.ship.duplicate_deck(self.current)
        self.current += 1
        self._after_deck_change()

    def rename_deck(self, index: int, name: str) -> None:
        name = name.strip()
        if name and name != self.ship.decks[index].name:
            self.ship.decks[index].name = name
            self._after_deck_change()

    def rename_deck_prompt(self, index: int) -> None:
        if index < 0:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename deck", "Deck name:", text=self.ship.decks[index].name
        )
        if accepted:
            self.rename_deck(index, name)

    def delete_deck(self) -> None:
        if len(self.ship.decks) == 1:
            return
        deck = self.deck
        if deck.parts:
            answer = QMessageBox.question(
                self,
                APP_NAME,
                f"Delete {deck.name} and its {len(deck.parts)} parts?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return
        if self.trace_deck is deck:
            self.trace_deck = None
        self.ship.delete_deck(self.current)
        self.current = min(self.current, len(self.ship.decks) - 1)
        self._after_deck_change()

    def _deck_tab_moved(self, source: int, destination: int) -> None:
        self.ship.reorder_deck(source, destination)
        self.current = self.deck_tabs.currentIndex()
        self.dirty = True
        self._refresh_decks()
        self._refresh_all()

    def _trace_chosen(self, index: int) -> None:
        self.trace_deck = self.trace_combo.itemData(index)
        self._rebuild_items(select=self.selected_indices())

    def turn_bow(self, quarter_turns: int) -> None:
        self.ship.bow = model.turn_bow(self.ship.bow, quarter_turns)
        self.orientation.set_bow(self.ship.bow)
        self.dirty = True
        self._refresh_title()

    def _orientation_toggled(self, shown: bool) -> None:
        self.orientation.setVisible(shown)
        self.turn_bow_left_action.setEnabled(shown)
        self.turn_bow_right_action.setEnabled(shown)

    def _name_edited(self, text: str) -> None:
        self.ship.name = text
        self._changed()

    # Files

    def _set_ship(self, ship: Ship, path: Path | None) -> None:
        self.ship = ship
        self.current = 0
        self.trace_deck = None
        self.path = path
        self.dirty = False
        self.name_edit.setText(ship.name)
        self.orientation.set_bow(ship.bow)
        self._refresh_decks()
        self._rebuild_items()

    def _ok_to_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Save changes to {self.ship.name}?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Save:
            return self.save_ship()
        return answer == QMessageBox.Discard

    def new_ship(self) -> None:
        if self._ok_to_discard():
            self._set_ship(Ship.empty(), None)

    def open_path(self, path: Path) -> bool:
        try:
            ship = Ship.load(path, self.library)
        except model.ShipFileError as error:
            QMessageBox.warning(self, APP_NAME, f"Could not load ship.\n\n{error}")
            return False
        self._set_ship(ship, Path(path))
        return True

    def open_ship(self) -> None:
        if not self._ok_to_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ship", "", "Ship files (*.json)"
        )
        if path:
            self.open_path(Path(path))

    def _default_filename(self, extension: str, deck: Deck | None = None) -> str:
        parts = self.ship.parts if deck is None else deck.parts
        tons = model.total_tons(model.tonnage(parts, self.library))
        day = datetime.date.today().isoformat()
        return model.suggested_filename(
            self.ship, tons, day, extension, deck.name if deck else None
        )

    def save_to(self, path: Path) -> bool:
        try:
            self.ship.save(path)
        except OSError as error:
            QMessageBox.warning(self, APP_NAME, f"Could not save ship.\n\n{error}")
            return False
        self.path = Path(path)
        self.dirty = False
        self._refresh_title()
        return True

    def save_ship(self) -> bool:
        return self.save_to(self.path) if self.path else self.save_ship_as()

    def save_ship_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ship", self._default_filename("json"), "Ship files (*.json)"
        )
        return self.save_to(Path(path)) if path else False

    def render_image(self, decks: list[Deck] | None = None) -> QImage | None:
        """Decks as one image, one above another, on the same map area so
        that they line up. The image credit goes underneath.

        Decks are drawn from scratch, so the selection and any tracing
        on the screen stay out of the picture.
        """
        decks = [self.deck] if decks is None else decks
        decks = [deck for deck in decks if deck.parts]
        if not decks:
            return None

        high_res = self.high_res_action.isChecked()
        images = self.high_res if high_res else self.images
        scenes = []
        area = QRectF()
        for deck in decks:
            scene = ShipScene()
            for z, part in enumerate(deck.parts):
                item = PartItem(
                    part, self.library[part.code], images, lambda moved: None
                )
                item.setZValue(z)
                scene.addItem(item)
                area = area.united(item.sceneBoundingRect())
            scenes.append(scene)
        margin = EXPORT_MARGIN_UNITS
        area.adjust(-margin, -margin, margin, margin)

        labelled = len(self.ship.decks) > 1
        full_height = area.height() * len(decks)
        scale = min(
            EXPORT_HIGH_RES_PIXELS_PER_UNIT if high_res else EXPORT_PIXELS_PER_UNIT,
            EXPORT_MAX_SIDE_PIXELS / max(area.width(), full_height),
            math.sqrt(EXPORT_MAX_TOTAL_PIXELS / (area.width() * full_height)),
        )
        width = max(math.ceil(area.width() * scale), 480)
        height = math.ceil(area.height() * scale)
        label_height = EXPORT_LABEL_PIXELS if labelled else 0
        total_height = (height + label_height) * len(decks) + EXPORT_CREDIT_PIXELS
        image = QImage(width, total_height, QImage.Format_RGB32)
        if image.isNull():
            raise MemoryError("Not enough memory for an image this large.")
        image.fill(QColor("white"))

        painter = QPainter(image)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        label_font = QFont()
        label_font.setPixelSize(EXPORT_LABEL_PIXELS - 14)
        label_font.setBold(True)
        credit_font = QFont()
        credit_font.setPixelSize(13)

        top = 0
        for deck, scene in zip(decks, scenes):
            if labelled:
                painter.setPen(QColor("#222222"))
                painter.setFont(label_font)
                painter.drawText(
                    QRectF(8, top, width - 16, label_height),
                    Qt.AlignVCenter | Qt.AlignLeft,
                    deck.name,
                )
            top += label_height
            target = QRectF(0, top, area.width() * scale, height)
            scene.render(painter, target, area)
            top += height

        painter.setPen(QColor("#444444"))
        painter.setFont(credit_font)
        painter.drawText(
            QRectF(8, top, width - 16, EXPORT_CREDIT_PIXELS),
            Qt.AlignVCenter | Qt.AlignLeft,
            EXPORT_CREDIT,
        )
        painter.end()
        return image

    def _export(self, decks: list[Deck] | None, filename: str) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            image = self.render_image(decks)
        except MemoryError as error:
            QMessageBox.warning(self, APP_NAME, str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        if image is None:
            QMessageBox.information(self, APP_NAME, "Add some parts first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export image", filename, "PNG image (*.png)"
        )
        if path and not image.save(path, "PNG"):
            QMessageBox.warning(self, APP_NAME, "Could not save the image.")

    def export_image(self) -> None:
        deck = self.deck if len(self.ship.decks) > 1 else None
        self._export(None, self._default_filename("png", deck))

    def export_all_decks(self) -> None:
        self._export(self.ship.decks, self._default_filename("png"))

    def closeEvent(self, event) -> None:
        if self._ok_to_discard():
            self.scene.selectionChanged.disconnect(self._selection_changed)
            event.accept()
        else:
            event.ignore()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow(model.load_library(DATA_DIR))
    window.show()
    return app.exec()
