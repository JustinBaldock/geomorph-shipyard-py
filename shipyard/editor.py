"""The Component Editor: build new library parts from cut subparts.

Drag subparts onto the canvas, where they snap to the grid, then save the
result as a new part. The canvas has the same two-square border as a real
geomorph tile, so a saved part behaves like any other part in the library.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
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

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            return QPointF(snapped(value.x(), self.step), snapped(value.y(), self.step))
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._moved()
        return super().itemChange(change, value)

    def to_json(self) -> dict:
        return {
            "subpart": self.name,
            "x": int(self.pos().x()),
            "y": int(self.pos().y()),
            "turns": self.turns,
            "mirror": self.mirror,
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

    def __init__(self, scene: QGraphicsScene):
        super().__init__(scene)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.scale(START_SCALE, START_SCALE)

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


def compose(items: list[SubpartItem], size: QSize) -> QImage:
    """Flatten subparts into one transparent image, lowest first."""
    image = QImage(size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    for item in sorted(items, key=lambda entry: entry.zValue()):
        painter.save()
        painter.translate(item.pos())
        painter.setTransform(item.transform(), True)
        painter.drawPixmap(0, 0, item.pixmap())
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
        self.setWindowTitle("Component Editor")

        self.scene = CanvasScene()
        self.scene.set_size(DEFAULT_SIZE_SQUARES, DEFAULT_SIZE_SQUARES)
        self.view = CanvasView(self.scene)
        self.view.dropped.connect(self.add_subpart)
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

    def _build_subpart_panel(self) -> QWidget:
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search subparts")
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

    def items(self) -> list[SubpartItem]:
        return [item for item in self.scene.items() if isinstance(item, SubpartItem)]

    def selected(self) -> list[SubpartItem]:
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
        ):
            action.setEnabled(chosen)

    def _size_changed(self) -> None:
        self.scene.set_size(self.width_spin.value(), self.height_spin.value())
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
            self.subpart_list.addItem(item)
        self._filter_subparts(self.search.text())

    def _filter_subparts(self, text: str) -> None:
        text = text.strip().casefold()
        shown = 0
        for row in range(self.subpart_list.count()):
            item = self.subpart_list.item(row)
            hide = text not in item.text().casefold()
            item.setHidden(hide)
            shown += not hide
        self.subpart_count.setText(
            f"{shown} of {self.subpart_list.count()} subparts"
        )

    def _custom_entries(self) -> dict:
        try:
            return custom.load_entries(self.custom_folder)
        except custom.CustomFileError as error:
            self.statusBar().showMessage(str(error))
            return {}

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

    def turn_selected(self, turns: int) -> None:
        for item in self.selected():
            item.set_pose(item.turns + turns, item.mirror)
        self._mark_changed()

    def flip_selected(self) -> None:
        for item in self.selected():
            item.set_pose(item.turns, not item.mirror)
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
            name = placed["subpart"]
            pixmap = QPixmap(str(custom.subpart_path(name, self.subpart_folder)))
            if pixmap.isNull():
                missing.append(name)
                continue
            item = self._place(name, pixmap)
            item.set_pose(placed["turns"], placed["mirror"])
            item.setPos(placed["x"], placed["y"])
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
        if code in self.library and code not in entries:
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

    def closeEvent(self, event) -> None:
        if self._ok_to_discard():
            self.scene.selectionChanged.disconnect(self._refresh_actions)
            event.accept()
        else:
            event.ignore()
