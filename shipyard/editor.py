"""The Component Editor: build new library parts from cut subparts.

Drag subparts onto the canvas, where they snap to the grid, then save the
result as a new part. The canvas has the same two-square border as a real
geomorph tile, so a saved part behaves like any other part in the library.
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QEvent, QLineF, QMimeData, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from . import custom, model
from .images import ImageStore

SQUARE = custom.PIXELS_PER_SQUARE
GUTTER = custom.GUTTER_SQUARES * SQUARE
SUBPART_MIME = "application/x-geomorph-subpart"
SNAP_CHOICES = (
    ("1 square", SQUARE),
    ("1/2 square", SQUARE // 2),
    ("1/4 square", SQUARE // 4),
)
PASTE_OFFSET = SQUARE

# Measured from the high-res tiles, in pixels.
HULL_WALL = 38
INTERIOR_WALL = 19
DOOR_GAP = 283
DOOR_LENGTH = 222
DOOR_WIDTH = 41
DOOR_LINE = 6
# Wide enough to clear a hull wall from behind a door.
DOOR_CLEARANCE = 46
DOOR_REACH = 190

BORDER_CHOICES = (("Hull wall", HULL_WALL), ("Interior wall", INTERIOR_WALL))
# Borders stay above the pieces, and doors stay above the borders.
BORDER_LAYER = 100_000
DOOR_LAYER = 200_000
BRUSH_STEP_PIXELS = 40
THUMBNAIL_PIXELS = 96
DEFAULT_SIZE_SQUARES = 10
MAX_SIZE_SQUARES = 100
MIN_SCALE = 0.02
MAX_SCALE = 2.0
START_SCALE = 0.1
USE_CHOICES = tuple(
    sorted({*model.USAGE_FILTERS, "Aerofin", "Workshop"} - {"Turret"})
)


def snapped(value: float, step: int) -> int:
    return round(value / step) * step


def door_along(value: float, step: int) -> float:
    """Where a door centres along its wall: mid-square, or on a finer step."""
    if step == SQUARE:
        return (value // SQUARE) * SQUARE + SQUARE / 2
    return snapped(value, step)


def door_slot(x: float, y: float, step: int) -> tuple[float, float, bool]:
    """The door position nearest a point, as (x, y, vertical).

    Doors sit on grid lines, so the nearest line decides which way one faces.
    """
    across_x = abs(x - snapped(x, SQUARE))
    across_y = abs(y - snapped(y, SQUARE))
    if across_x < across_y:
        return snapped(x, SQUARE), door_along(y, step), True
    return door_along(x, step), snapped(y, SQUARE), False


def canvas_size(squares_x: int, squares_y: int) -> QSize:
    """The full image size for a part, including its border."""
    extra = 2 * custom.GUTTER_SQUARES
    return QSize((squares_x + extra) * SQUARE, (squares_y + extra) * SQUARE)


class SubpartItem(QGraphicsPixmapItem):
    """A subpart on the canvas. Its position is the top left of what you see."""

    def __init__(self, name: str, pixmap: QPixmap, step: int, moved):
        super().__init__(pixmap)
        self.name = name
        self.step = step
        self.turns = 0
        self.mirror = False
        self._moved = moved
        self.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
        self.setTransformationMode(Qt.SmoothTransformation)
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

    def set_pose(self, turns: int, mirror: bool) -> None:
        """Turn or flip it, keeping its top left corner where it is."""
        self.turns = turns % 4
        self.mirror = mirror
        turn = QTransform()
        turn.rotate(90 * self.turns)
        if mirror:
            turn.scale(-1, 1)
        area = turn.mapRect(QRectF(self.pixmap().rect()))
        self.setTransform(turn * QTransform.fromTranslate(-area.x(), -area.y()))

    @staticmethod
    def _treat_shift_as_multi_select(event) -> None:
        # Qt adds to a selection with Ctrl (Cmd on a Mac), as in the main app.
        if event.modifiers() & Qt.ShiftModifier:
            event.setModifiers(event.modifiers() | Qt.ControlModifier)

    def mousePressEvent(self, event) -> None:
        self._treat_shift_as_multi_select(event)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._treat_shift_as_multi_select(event)
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            return QPointF(snapped(value.x(), self.step), snapped(value.y(), self.step))
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._moved()
        return super().itemChange(change, value)

    def visible_rect(self) -> QRectF:
        """The area the subpart covers on the canvas."""
        return self.transform().mapRect(QRectF(self.pixmap().rect())).translated(
            self.pos()
        )

    def draw_flat(self, painter: QPainter) -> None:
        painter.setTransform(self.transform(), True)
        painter.drawPixmap(0, 0, self.pixmap())

    def to_json(self) -> dict:
        return {
            "subpart": self.name,
            "x": int(self.pos().x()),
            "y": int(self.pos().y()),
            "turns": self.turns,
            "mirror": self.mirror,
        }


class DrawnItem(QGraphicsItem):
    """Something the editor draws itself, instead of a cut image."""

    def __init__(self, step: int):
        super().__init__()
        self.step = step
        self.setFlags(
            QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges
        )

    def mousePressEvent(self, event) -> None:
        SubpartItem._treat_shift_as_multi_select(event)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        SubpartItem._treat_shift_as_multi_select(event)
        super().mouseReleaseEvent(event)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        self.draw_flat(painter)
        painter.restore()
        if self.isSelected():
            pen = QPen(QColor("#2a6fff"), 0, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self.shape())


class BorderItem(DrawnItem):
    """A wall around an area. Its position is the area's top left corner."""

    name = "border"

    def __init__(
        self, step: int, width: int, height: int, thickness: int, follows_part: bool
    ):
        super().__init__(step)
        self.width = width
        self.height = height
        self.thickness = thickness
        self.follows_part = follows_part
        self.moved = lambda: None
        self.setFlag(QGraphicsItem.ItemIsMovable)

    def area(self) -> QRectF:
        return QRectF(self.pos().x(), self.pos().y(), self.width, self.height)

    def set_area(self, area: QRectF) -> None:
        self.prepareGeometryChange()
        self.width = int(area.width())
        self.height = int(area.height())
        self.setPos(area.topLeft())
        self.update()

    def boundingRect(self) -> QRectF:
        margin = self.thickness
        return QRectF(0, 0, self.width, self.height).adjusted(
            -margin, -margin, margin, margin
        )

    def shape(self) -> QPainterPath:
        # Only the wall is clickable, so pieces inside it can still be reached.
        path = QPainterPath()
        path.addRect(QRectF(0, 0, self.width, self.height))
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self.thickness, 40))
        return stroker.createStroke(path)

    def draw_flat(self, painter: QPainter) -> None:
        pen = QPen(QColor("black"), self.thickness)
        pen.setJoinStyle(Qt.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(0, 0, self.width, self.height))

    def lies_on(self, x: float, y: float, vertical: bool) -> bool:
        """Whether a door at this point would sit in this wall."""
        area = self.area()
        if vertical:
            return x in (area.left(), area.right()) and area.top() <= y <= area.bottom()
        return y in (area.top(), area.bottom()) and area.left() <= x <= area.right()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            return QPointF(snapped(value.x(), self.step), snapped(value.y(), self.step))
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.moved()
        return super().itemChange(change, value)

    def to_json(self) -> dict:
        return {
            "kind": "border",
            "x": int(self.pos().x()),
            "y": int(self.pos().y()),
            "width": self.width,
            "height": self.height,
            "thickness": self.thickness,
            "follows_part": self.follows_part,
        }


class DoorItem(DrawnItem):
    """A door set into a wall. Its position is the middle of the door."""

    name = "door"

    def __init__(self, step: int, vertical: bool, wall: int):
        super().__init__(step)
        self.vertical = vertical
        self.wall = wall

    def boundingRect(self) -> QRectF:
        reach = DOOR_REACH
        side = DOOR_CLEARANCE
        if self.vertical:
            return QRectF(-side, -reach, 2 * side, 2 * reach)
        return QRectF(-reach, -side, 2 * reach, 2 * side)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def draw_flat(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        if self.vertical:
            painter.rotate(90)
        # The tiles finish a wall with a rounded end on each side of a door,
        # and the gap is measured between the tips of those ends.
        radius = self.wall / 2
        end = DOOR_GAP / 2 + radius
        painter.fillRect(
            QRectF(-end, -DOOR_CLEARANCE / 2, 2 * end, DOOR_CLEARANCE),
            QColor("white"),
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("black"))
        painter.drawEllipse(QPointF(-end, 0), radius, radius)
        painter.drawEllipse(QPointF(end, 0), radius, radius)
        painter.setPen(QPen(QColor("black"), DOOR_LINE))
        painter.setBrush(QColor("white"))
        painter.drawRect(
            QRectF(-DOOR_LENGTH / 2, -DOOR_WIDTH / 2, DOOR_LENGTH, DOOR_WIDTH)
        )

    def to_json(self) -> dict:
        return {
            "kind": "door",
            "x": self.pos().x(),
            "y": self.pos().y(),
            "vertical": self.vertical,
            "wall": self.wall,
        }


class CanvasScene(QGraphicsScene):
    """Shades the tile border and draws the square grid over the artwork."""

    def __init__(self):
        super().__init__()
        self.squares_x = DEFAULT_SIZE_SQUARES
        self.squares_y = DEFAULT_SIZE_SQUARES

    def set_size(self, squares_x: int, squares_y: int) -> None:
        self.squares_x = squares_x
        self.squares_y = squares_y
        size = canvas_size(squares_x, squares_y)
        self.setSceneRect(
            QRectF(-SQUARE, -SQUARE, size.width() + 2 * SQUARE, size.height() + 2 * SQUARE)
        )
        self.update()

    def full_rect(self) -> QRectF:
        return QRectF(QPointF(0, 0), canvas_size(self.squares_x, self.squares_y))

    def part_rect(self) -> QRectF:
        return self.full_rect().adjusted(GUTTER, GUTTER, -GUTTER, -GUTTER)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor("#c9d0d7"))
        painter.fillRect(self.full_rect(), QColor("#e8ecef"))
        painter.fillRect(self.part_rect(), QColor("#ffffff"))

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        full = self.full_rect()
        pen = QPen(QColor(42, 111, 255, 60))
        pen.setCosmetic(True)
        painter.setPen(pen)
        x = 0
        while x <= full.width():
            painter.drawLine(QPointF(x, 0), QPointF(x, full.height()))
            x += SQUARE
        y = 0
        while y <= full.height():
            painter.drawLine(QPointF(0, y), QPointF(full.width(), y))
            y += SQUARE
        edge = QPen(QColor("#2a6fff"))
        edge.setCosmetic(True)
        edge.setWidth(2)
        painter.setPen(edge)
        painter.drawRect(self.part_rect())


class SubpartList(QListWidget):
    """Thumbnails that can be dragged onto the canvas."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setIconSize(QSize(THUMBNAIL_PIXELS, THUMBNAIL_PIXELS))
        self.setGridSize(QSize(THUMBNAIL_PIXELS + 24, THUMBNAIL_PIXELS + 34))
        self.setWordWrap(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)

    def mimeTypes(self) -> list[str]:
        return [SUBPART_MIME]

    def mimeData(self, items) -> QMimeData:
        data = QMimeData()
        if items:
            data.setData(SUBPART_MIME, items[0].text().encode("utf-8"))
        return data


class CanvasView(QGraphicsView):
    """Accepts subparts dropped from the list. Scroll to zoom."""

    dropped = Signal(str, QPointF)
    # The point, whether it starts a stroke, and whether it erases.
    brushed = Signal(QPointF, bool, bool)

    def __init__(self, scene: QGraphicsScene):
        super().__init__(scene)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.scale(START_SCALE, START_SCALE)
        self.brush_on = False
        self._stroke_point: QPointF | None = None
        self._stroke_erases = False

    def set_brush(self, on: bool) -> None:
        self.brush_on = on
        self.setDragMode(
            QGraphicsView.NoDrag if on else QGraphicsView.RubberBandDrag
        )
        self.viewport().setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        if not self.brush_on or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._stroke_point = self.mapToScene(event.position().toPoint())
        self._stroke_erases = bool(event.modifiers() & Qt.AltModifier)
        self.brushed.emit(self._stroke_point, True, self._stroke_erases)

    def mouseMoveEvent(self, event) -> None:
        if self._stroke_point is None:
            super().mouseMoveEvent(event)
            return
        # A fast drag jumps between mouse events, so fill in the points between.
        point = self.mapToScene(event.position().toPoint())
        line = QLineF(self._stroke_point, point)
        steps = max(1, int(line.length() / BRUSH_STEP_PIXELS))
        for number in range(1, steps + 1):
            self.brushed.emit(line.pointAt(number / steps), False, self._stroke_erases)
        self._stroke_point = point

    def mouseReleaseEvent(self, event) -> None:
        if self._stroke_point is None:
            super().mouseReleaseEvent(event)
            return
        self._stroke_point = None

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(SUBPART_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(SUBPART_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasFormat(SUBPART_MIME):
            return
        name = bytes(event.mimeData().data(SUBPART_MIME)).decode("utf-8")
        point = self.mapToScene(event.position().toPoint())
        self.dropped.emit(name, point)
        event.acceptProposedAction()

    def wheelEvent(self, event) -> None:
        wanted = 2 ** (event.angleDelta().y() / 240)
        current = self.transform().m11()
        self.scale(*[max(MIN_SCALE / current, min(MAX_SCALE / current, wanted))] * 2)
        event.accept()


def compose(items: list, size: QSize) -> QImage:
    """Flatten everything on the canvas into one image, lowest first."""
    image = QImage(size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    for item in sorted(items, key=lambda entry: entry.zValue()):
        painter.save()
        painter.translate(item.pos())
        item.draw_flat(painter)
        painter.restore()
    painter.end()
    return image


class ComponentEditor(QMainWindow):
    saved = Signal(str)

    def __init__(
        self,
        library: dict,
        subpart_folder: Path = custom.SUBPART_FOLDER,
        custom_folder: Path = custom.CUSTOM_FOLDER,
    ):
        super().__init__()
        ImageStore([])  # applies the memory limit large subparts may need
        self.library = library
        self.subpart_folder = Path(subpart_folder)
        self.custom_folder = Path(custom_folder)
        self.dirty = False
        self.step = SQUARE
        self.next_z = 0.0
        self.clipboard: list[dict] = []
        self.paste_count = 0
        self.setWindowTitle("Component Editor")

        self.scene = CanvasScene()
        self.scene.set_size(DEFAULT_SIZE_SQUARES, DEFAULT_SIZE_SQUARES)
        self.view = CanvasView(self.scene)
        self.view.dropped.connect(self.add_subpart)
        self.view.brushed.connect(self.brush_door)
        self._stroke: tuple[bool, float] | None = None
        self.scene.selectionChanged.connect(self._refresh_actions)

        self._build_actions()
        left = self._build_subpart_panel()
        right = self._build_details_panel()

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(left)
        layout.addWidget(self.view, 1)
        layout.addWidget(right)
        self.setCentralWidget(central)

        self.resize(1400, 860)
        self.refresh_subparts()
        self._refresh_saved_parts()
        self._new_code()
        self._refresh_actions()
        self._refresh_tons()
        self.view.centerOn(self.scene.full_rect().center())
        self.dirty = False

    # Setting up

    def _action(self, text, handler, shortcuts=()) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda checked=False: handler())
        action.setShortcuts(list(shortcuts))
        self.addAction(action)
        return action

    def _build_actions(self) -> None:
        a = self._action
        self.left_action = a("Rotate left", lambda: self.turn_selected(-1), ["Shift+R"])
        self.right_action = a("Rotate right", lambda: self.turn_selected(1), ["R"])
        self.flip_action = a("Flip", self.flip_selected, ["S"])
        self.front_action = a("Bring to front", self.bring_to_front, ["]"])
        self.back_action = a("Send to back", self.send_to_back, ["["])
        self.delete_action = a(
            "Delete", self.delete_selected, [Qt.Key_Delete, Qt.Key_Backspace]
        )
        self.cut_action = a("Cut", self.cut_selected, [QKeySequence.Cut])
        self.copy_action = a("Copy", self.copy_selected, [QKeySequence.Copy])
        self.paste_action = a("Paste", self.paste, [QKeySequence.Paste])
        self.select_all_action = a(
            "Select all", self.select_all, [QKeySequence.SelectAll]
        )
        self.border_action = a("Draw border", self.draw_border, ["B"])
        self.door_action = QAction("Door brush", self)
        self.door_action.setCheckable(True)
        self.door_action.setShortcut("D")
        self.door_action.setToolTip(
            "Click or drag along a wall to add doors. Option-click removes one."
        )
        self.door_action.toggled.connect(self.view.set_brush)
        self.addAction(self.door_action)
        self.brush_off_action = a(
            "Stop brushing",
            lambda: self.door_action.setChecked(False),
            [Qt.Key_Escape],
        )
        self.save_action = a("Save component", self.save_component, [QKeySequence.Save])
        self.new_action = a("New component", self.new_component, [QKeySequence.New])

        bar = QToolBar("Component")
        bar.setMovable(False)
        self.addToolBar(bar)
        for action in (
            self.left_action,
            self.right_action,
            self.flip_action,
            self.front_action,
            self.back_action,
            self.delete_action,
        ):
            bar.addAction(action)
        bar.addSeparator()
        bar.addAction(self.copy_action)
        bar.addAction(self.paste_action)
        bar.addSeparator()
        bar.addAction(self.border_action)
        bar.addAction(self.door_action)

    def _build_subpart_panel(self) -> QWidget:
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names and categories")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_subparts)
        self.subpart_list = SubpartList()
        self.subpart_list.itemDoubleClicked.connect(
            lambda item: self.add_subpart(item.text(), self._view_centre())
        )
        refresh = QPushButton("Refresh list")
        refresh.clicked.connect(lambda: self.refresh_subparts())
        self.subpart_count = QLabel()

        panel = QWidget()
        panel.setFixedWidth(300)
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("Drag subparts onto the canvas"))
        layout.addWidget(self.search)
        layout.addWidget(self.subpart_list, 1)
        layout.addWidget(self.subpart_count)
        layout.addWidget(refresh)
        return panel

    def _build_details_panel(self) -> QWidget:
        self.saved_combo = QComboBox()
        self.saved_combo.activated.connect(self._saved_part_chosen)
        self.code_edit = QLineEdit()
        self.code_edit.textEdited.connect(lambda _text: self._mark_changed())
        self.width_spin = self._size_spin()
        self.height_spin = self._size_spin()
        self.snap_combo = QComboBox()
        for label, step in SNAP_CHOICES:
            self.snap_combo.addItem(label, step)
        self.snap_combo.currentIndexChanged.connect(self._snap_chosen)

        form = QFormLayout()
        form.addRow("Open saved part", self.saved_combo)
        form.addRow("Code", self.code_edit)
        form.addRow("Width (squares)", self.width_spin)
        form.addRow("Height (squares)", self.height_spin)
        form.addRow("Snap to", self.snap_combo)
        self.border_combo = QComboBox()
        for label, thickness in BORDER_CHOICES:
            self.border_combo.addItem(label, thickness)
        form.addRow("Border", self.border_combo)

        self.use_table = QTableWidget(len(USE_CHOICES), 2)
        self.use_table.setHorizontalHeaderLabels(["Use", "dTon"])
        self.use_table.verticalHeader().hide()
        self.use_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.use_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.use_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self.use_spins: dict[str, QSpinBox] = {}
        for row, use in enumerate(USE_CHOICES):
            self.use_table.setItem(row, 0, QTableWidgetItem(use))
            spin = QSpinBox()
            spin.setRange(0, 100_000)
            spin.valueChanged.connect(lambda _value: self._uses_changed())
            self.use_table.setCellWidget(row, 1, spin)
            self.use_spins[use] = spin

        self.tons_label = QLabel()
        self.save_button = QPushButton("Save component")
        self.save_button.clicked.connect(lambda: self.save_component())
        new_button = QPushButton("New component")
        new_button.clicked.connect(lambda: self.new_component())

        panel = QWidget()
        panel.setFixedWidth(320)
        layout = QVBoxLayout(panel)
        layout.addLayout(form)
        layout.addWidget(self.use_table, 1)
        layout.addWidget(self.tons_label)
        layout.addWidget(self.save_button)
        layout.addWidget(new_button)
        return panel

    def _size_spin(self) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, MAX_SIZE_SQUARES)
        spin.setValue(DEFAULT_SIZE_SQUARES)
        spin.valueChanged.connect(lambda _value: self._size_changed())
        return spin

    # Keeping everything in step

    def items(self) -> list:
        kinds = (SubpartItem, BorderItem, DoorItem)
        return [item for item in self.scene.items() if isinstance(item, kinds)]

    def selected(self) -> list:
        return [item for item in self.items() if item.isSelected()]

    def _mark_changed(self) -> None:
        self.dirty = True
        self.setWindowTitle("Component Editor *")

    def _refresh_actions(self) -> None:
        chosen = bool(self.selected())
        for action in (
            self.left_action,
            self.right_action,
            self.flip_action,
            self.front_action,
            self.back_action,
            self.delete_action,
            self.cut_action,
            self.copy_action,
        ):
            action.setEnabled(chosen)
        self.paste_action.setEnabled(bool(self.clipboard))

    def _size_changed(self) -> None:
        self.scene.set_size(self.width_spin.value(), self.height_spin.value())
        for item in self.items():
            if isinstance(item, BorderItem) and item.follows_part:
                item.set_area(self.scene.part_rect())
        self._mark_changed()
        self._refresh_tons()

    def _uses(self) -> dict[str, int]:
        return {
            use: spin.value() for use, spin in self.use_spins.items() if spin.value()
        }

    def _uses_changed(self) -> None:
        self._mark_changed()
        self._refresh_tons()

    def _tons(self) -> int:
        uses = self._uses()
        if uses:
            return sum(uses.values())
        area = self.width_spin.value() * self.height_spin.value()
        return area // custom.SQUARES_PER_DTON

    def _refresh_tons(self) -> None:
        source = "from the uses above" if self._uses() else "from the part's area"
        self.tons_label.setText(f"{self._tons()} dTon, {source}")

    def _snap_chosen(self, index: int) -> None:
        self.step = self.snap_combo.itemData(index)
        for item in self.items():
            item.step = self.step

    def _thumbnail(self, path: Path) -> QPixmap:
        reader = QImageReader(str(path))
        size = reader.size()
        size.scale(QSize(THUMBNAIL_PIXELS, THUMBNAIL_PIXELS), Qt.KeepAspectRatio)
        reader.setScaledSize(size)
        return QPixmap.fromImage(reader.read())

    def refresh_subparts(self) -> None:
        self.subpart_list.clear()
        for name in custom.subpart_names(self.subpart_folder):
            path = custom.subpart_path(name, self.subpart_folder)
            item = QListWidgetItem(QIcon(self._thumbnail(path)), name)
            categories = custom.subpart_categories(name, self.subpart_folder)
            item.setData(Qt.UserRole, " ".join(categories))
            if categories:
                item.setToolTip(", ".join(categories))
            self.subpart_list.addItem(item)
        self._filter_subparts(self.search.text())

    def _filter_subparts(self, text: str) -> None:
        text = text.strip().casefold()
        shown = 0
        for row in range(self.subpart_list.count()):
            item = self.subpart_list.item(row)
            searched = f"{item.text()} {item.data(Qt.UserRole)}".casefold()
            hide = text not in searched
            item.setHidden(hide)
            shown += not hide
        self.subpart_count.setText(
            f"{shown} of {self.subpart_list.count()} subparts"
        )

    def _custom_entries(self) -> dict:
        problems: list[str] = []
        entries = custom.load_entries(self.custom_folder, problems)
        if problems:
            self.statusBar().showMessage(
                "Could not read: " + ", ".join(problems)
            )
        return entries

    def _refresh_saved_parts(self) -> None:
        self.saved_combo.clear()
        self.saved_combo.addItem("Choose...", None)
        for code in sorted(self._custom_entries()):
            self.saved_combo.addItem(code, code)

    def _new_code(self) -> None:
        self.code_edit.setText(
            custom.next_code({*self.library, *self._custom_entries()})
        )

    def _view_centre(self) -> QPointF:
        return self.view.mapToScene(self.view.viewport().rect().center())

    # What the user can do

    def add_subpart(self, name: str, point: QPointF) -> SubpartItem | None:
        path = custom.subpart_path(name, self.subpart_folder)
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            QMessageBox.warning(self, "Component Editor", f"Could not open {name}.")
            return None
        item = self._place(name, pixmap)
        item.setPos(
            point - QPointF(pixmap.width() / 2, pixmap.height() / 2)
        )
        self.scene.clearSelection()
        item.setSelected(True)
        self.view.setFocus()
        self._mark_changed()
        return item

    def _place(self, name: str, pixmap: QPixmap) -> SubpartItem:
        item = SubpartItem(name, pixmap, self.step, self._mark_changed)
        item.setZValue(self.next_z)
        self.next_z += 1
        self.scene.addItem(item)
        return item

    def _selected_subparts(self) -> list[SubpartItem]:
        return [item for item in self.selected() if isinstance(item, SubpartItem)]

    def turn_selected(self, turns: int) -> None:
        for item in self._selected_subparts():
            item.set_pose(item.turns + turns, item.mirror)
        self._mark_changed()

    def flip_selected(self) -> None:
        for item in self._selected_subparts():
            item.set_pose(item.turns, not item.mirror)
        self._mark_changed()

    def _border_area(self) -> tuple[QRectF, bool]:
        """Around the selected pieces, or else the whole part."""
        chosen = self._selected_subparts()
        if not chosen:
            return self.scene.part_rect(), True
        area = chosen[0].visible_rect()
        for item in chosen[1:]:
            area = area.united(item.visible_rect())
        # Each edge snaps outwards, so the pieces always end up inside.
        step = self.step
        return (
            QRectF(
                QPointF(
                    math.floor(area.left() / step) * step,
                    math.floor(area.top() / step) * step,
                ),
                QPointF(
                    math.ceil(area.right() / step) * step,
                    math.ceil(area.bottom() / step) * step,
                ),
            ),
            False,
        )

    def _add_border(self, area: QRectF, thickness: int, follows_part: bool) -> BorderItem:
        item = BorderItem(
            self.step, int(area.width()), int(area.height()), thickness, follows_part
        )
        item.moved = self._mark_changed
        item.setZValue(BORDER_LAYER + self.next_z)
        self.next_z += 1
        self.scene.addItem(item)
        item.setPos(area.topLeft())
        return item

    def draw_border(self) -> BorderItem:
        area, whole_part = self._border_area()
        item = self._add_border(area, self.border_combo.currentData(), whole_part)
        self.view.setFocus()
        self._mark_changed()
        return item

    def _door_at(self, x: float, y: float, vertical: bool) -> DoorItem | None:
        for item in self.items():
            if (
                isinstance(item, DoorItem)
                and item.vertical == vertical
                and item.pos() == QPointF(x, y)
            ):
                return item
        return None

    def _wall_at(self, x: float, y: float, vertical: bool) -> int:
        """How thick the wall is here, so a door's rounded ends match it."""
        walls = [
            item.thickness
            for item in self.items()
            if isinstance(item, BorderItem) and item.lies_on(x, y, vertical)
        ]
        return max(walls, default=INTERIOR_WALL)

    def _add_door(
        self, x: float, y: float, vertical: bool, wall: int | None = None
    ) -> DoorItem:
        item = DoorItem(self.step, vertical, wall or self._wall_at(x, y, vertical))
        item.setPos(x, y)
        item.setZValue(DOOR_LAYER + self.next_z)
        self.next_z += 1
        self.scene.addItem(item)
        return item

    def brush_door(self, point: QPointF, first: bool, erase: bool) -> None:
        """Paint or erase a door, staying on one wall line for the whole drag."""
        x, y, vertical = door_slot(point.x(), point.y(), self.step)
        if first:
            self._stroke = (vertical, x if vertical else y)
        vertical, line = self._stroke
        if vertical:
            x, y = line, door_along(point.y(), self.step)
        else:
            x, y = door_along(point.x(), self.step), line

        door = self._door_at(x, y, vertical)
        if erase and door is not None:
            self.scene.removeItem(door)
            self._mark_changed()
        elif not erase and door is None:
            self._add_door(x, y, vertical)
            self._mark_changed()

    def bring_to_front(self) -> None:
        for item in sorted(self.selected(), key=lambda entry: entry.zValue()):
            item.setZValue(self.next_z)
            self.next_z += 1
        self._mark_changed()

    def send_to_back(self) -> None:
        lowest = min((item.zValue() for item in self.items()), default=0.0)
        for item in sorted(
            self.selected(), key=lambda entry: entry.zValue(), reverse=True
        ):
            lowest -= 1
            item.setZValue(lowest)
        self._mark_changed()

    def delete_selected(self) -> None:
        for item in self.selected():
            self.scene.removeItem(item)
        self._mark_changed()
        self._refresh_actions()

    def select_all(self) -> None:
        for item in self.items():
            item.setSelected(True)

    def copy_selected(self) -> None:
        """Keep the selected pieces, lowest layer first, with their pose."""
        chosen = sorted(self.selected(), key=lambda item: item.zValue())
        if not chosen:
            return
        self.clipboard = [
            {
                "data": item.to_json(),
                "pixmap": item.pixmap() if isinstance(item, SubpartItem) else None,
            }
            for item in chosen
        ]
        self.paste_count = 0
        self._refresh_actions()

    def cut_selected(self) -> None:
        self.copy_selected()
        self.delete_selected()

    def paste(self) -> None:
        """Add copies a square away, each paste a square further on."""
        if not self.clipboard:
            return
        self.paste_count += 1
        offset = self.paste_count * PASTE_OFFSET
        self.scene.clearSelection()
        for piece in self.clipboard:
            item = self._restore(piece["data"], piece["pixmap"])
            item.setPos(item.pos() + QPointF(offset, offset))
            if isinstance(item, BorderItem):
                item.follows_part = False  # a copy stays where it's put
            item.setSelected(True)
        self.view.setFocus()
        self._mark_changed()

    def _ok_to_discard(self) -> bool:
        if not self.dirty or not self.items():
            return True
        answer = QMessageBox.question(
            self,
            "Component Editor",
            "Discard the unsaved changes to this component?",
            QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return answer == QMessageBox.Discard

    def _clear(self) -> None:
        for item in self.items():
            self.scene.removeItem(item)
        for spin in self.use_spins.values():
            spin.setValue(0)
        self.next_z = 0.0

    def new_component(self) -> None:
        if not self._ok_to_discard():
            return
        self._clear()
        self.width_spin.setValue(DEFAULT_SIZE_SQUARES)
        self.height_spin.setValue(DEFAULT_SIZE_SQUARES)
        self._new_code()
        self.saved_combo.setCurrentIndex(0)
        self._set_clean()

    def _set_clean(self) -> None:
        self.dirty = False
        self.setWindowTitle("Component Editor")

    def _saved_part_chosen(self, index: int) -> None:
        code = self.saved_combo.itemData(index)
        if code and self._ok_to_discard():
            self.open_component(code)

    def _restore(self, data: dict, pixmap: QPixmap | None):
        """Rebuild something on the canvas from its saved form."""
        kind = data.get("kind")
        if kind == "border":
            area = QRectF(data["x"], data["y"], data["width"], data["height"])
            return self._add_border(area, data["thickness"], data["follows_part"])
        if kind == "door":
            return self._add_door(data["x"], data["y"], data["vertical"], data["wall"])
        item = self._place(data["subpart"], pixmap)
        item.set_pose(data["turns"], data["mirror"])
        item.setPos(data["x"], data["y"])
        return item

    def open_component(self, code: str) -> bool:
        entry = self._custom_entries().get(code)
        if entry is None:
            return False
        self._clear()
        self.code_edit.setText(code)
        self.width_spin.setValue(entry["sizeX"])
        self.height_spin.setValue(entry["sizeY"])
        for use, tons in entry.get("uses", {}).items():
            if use in self.use_spins:
                self.use_spins[use].setValue(tons)

        missing = []
        for placed in entry.get("layout", []):
            pixmap = None
            if "subpart" in placed:
                name = placed["subpart"]
                pixmap = QPixmap(str(custom.subpart_path(name, self.subpart_folder)))
                if pixmap.isNull():
                    missing.append(name)
                    continue
            self._restore(placed, pixmap)
        self._set_clean()
        if missing:
            QMessageBox.warning(
                self,
                "Component Editor",
                "These subparts are missing, so they were left out:\n"
                + "\n".join(sorted(set(missing))),
            )
        return True

    def save_component(self) -> str | None:
        code = custom.safe_name(self.code_edit.text())
        title = "Component Editor"
        if not code:
            QMessageBox.warning(self, title, "Give the component a code.")
            return None
        if not self.items():
            QMessageBox.warning(self, title, "Add at least one subpart first.")
            return None
        entries = self._custom_entries()
        built_in = code in self.library and (
            self.library[code].shape != model.CUSTOM_SHAPE
        )
        if built_in:
            QMessageBox.warning(
                self, title, f"{code} is already a part in the library."
            )
            return None
        if code in entries:
            answer = QMessageBox.question(
                self,
                title,
                f"Replace the saved part {code}?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return None

        size = canvas_size(self.width_spin.value(), self.height_spin.value())
        image = compose(self.items(), size)
        high, low = custom.image_paths(code, self.custom_folder)
        low.parent.mkdir(parents=True, exist_ok=True)
        smaller = image.scaled(
            round(size.width() * custom.LOW_RES_SCALE),
            round(size.height() * custom.LOW_RES_SCALE),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
        if not image.save(str(high), "PNG") or not smaller.save(str(low), "PNG"):
            QMessageBox.warning(self, title, "Could not save the images.")
            return None

        ordered = sorted(self.items(), key=lambda entry: entry.zValue())
        custom.save_entry(
            code,
            {
                "url": f"{code}.png",
                "sizeX": self.width_spin.value(),
                "sizeY": self.height_spin.value(),
                "tons": self._tons(),
                "uses": self._uses(),
                "facilities": {},
                "layout": [item.to_json() for item in ordered],
            },
            self.custom_folder,
        )
        self.code_edit.setText(code)
        self._refresh_saved_parts()
        self._set_clean()
        self.statusBar().showMessage(f"Saved {code}", 5000)
        self.saved.emit(code)
        return code

    def changeEvent(self, event) -> None:
        # Parts may have been deleted or changed in Finder in the meantime.
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._refresh_saved_parts()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        if self._ok_to_discard():
            self.scene.selectionChanged.disconnect(self._refresh_actions)
            event.accept()
        else:
            event.ignore()
