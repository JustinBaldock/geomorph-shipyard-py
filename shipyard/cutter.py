"""The Subpart Cutter: cut pieces out of the high-res geomorphs.

Run it on its own with `python -m shipyard.cutter`, or from the Tools menu in
the main app. Pieces are saved to assets/geomorph-hires-subpart, ready for the
Component Editor.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import custom, model
from .images import HIGH_RES_FOLDER, ImageStore

SQUARE = custom.PIXELS_PER_SQUARE
SNAP_CHOICES = (
    ("1 square", SQUARE),
    ("1/2 square", SQUARE // 2),
    ("1/4 square", SQUARE // 4),
    ("No snapping", 1),
)
MAX_ZOOM = 4.0
FIT_MARGIN_PIXELS = 20


def snapped(value: float, step: int, limit: int) -> int:
    """The nearest step, kept inside the image."""
    return max(0, min(limit, round(value / step) * step))


class CutterView(QGraphicsView):
    """Shows one image. Drag to mark a box, scroll to zoom."""

    selection_changed = Signal()

    def __init__(self):
        self._scene = QGraphicsScene()
        super().__init__(self._scene)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#d9dee3"))
        self.setDragMode(QGraphicsView.NoDrag)
        self.step = SQUARE
        self.pixmap: QPixmap | None = None
        self._start: QPointF | None = None

        self.image_item = QGraphicsPixmapItem()
        self.image_item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self.image_item)

        self.box = QGraphicsRectItem()
        pen = QPen(QColor("#2a6fff"))
        pen.setCosmetic(True)
        pen.setWidth(2)
        self.box.setPen(pen)
        self.box.setBrush(QBrush(QColor(42, 111, 255, 45)))
        self.box.setZValue(10)
        self.box.hide()
        self._scene.addItem(self.box)

    def show_image(self, pixmap: QPixmap | None) -> None:
        self.pixmap = pixmap
        self.clear_selection()
        if pixmap is None:
            self.image_item.setPixmap(QPixmap())
            return
        self.image_item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.fit()

    def fit(self) -> None:
        if self.pixmap is not None:
            self.fitInView(self.image_item, Qt.KeepAspectRatio)

    def clear_selection(self) -> None:
        self.box.setRect(QRectF())
        self.box.hide()
        self.selection_changed.emit()

    def selection(self) -> QRect:
        """The marked box in image pixels, empty if nothing is marked."""
        return self.box.rect().toRect() if self.box.isVisible() else QRect()

    def _snapped_point(self, event) -> QPointF:
        point = self.mapToScene(event.position().toPoint())
        return QPointF(
            snapped(point.x(), self.step, self.pixmap.width()),
            snapped(point.y(), self.step, self.pixmap.height()),
        )

    def mousePressEvent(self, event) -> None:
        if self.pixmap is None or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._start = self._snapped_point(event)
        self.box.setRect(QRectF(self._start, self._start))
        self.box.show()

    def mouseMoveEvent(self, event) -> None:
        if self._start is None:
            super().mouseMoveEvent(event)
            return
        self.box.setRect(QRectF(self._start, self._snapped_point(event)).normalized())
        self.selection_changed.emit()

    def mouseReleaseEvent(self, event) -> None:
        if self._start is None:
            super().mouseReleaseEvent(event)
            return
        self._start = None
        if self.box.rect().isEmpty():
            self.box.hide()
        self.selection_changed.emit()

    def wheelEvent(self, event) -> None:
        if self.pixmap is None:
            return
        wanted = 2 ** (event.angleDelta().y() / 240)
        current = self.transform().m11()
        # The smallest zoom is the one that fits the whole image in view.
        view = self.viewport().size()
        smallest = min(
            view.width() / self.pixmap.width(), view.height() / self.pixmap.height()
        )
        factor = max(smallest / current, min(MAX_ZOOM / current, wanted))
        self.scale(factor, factor)
        event.accept()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if self.pixmap is None or self.transform().m11() * SQUARE < 6:
            return
        pen = QPen(QColor(42, 111, 255, 70))
        pen.setCosmetic(True)
        painter.setPen(pen)
        width, height = self.pixmap.width(), self.pixmap.height()
        for x in range(0, width + 1, SQUARE):
            painter.drawLine(x, 0, x, height)
        for y in range(0, height + 1, SQUARE):
            painter.drawLine(0, y, width, y)


class CutterWindow(QMainWindow):
    saved = Signal(str)

    def __init__(
        self,
        source_folder: Path = HIGH_RES_FOLDER,
        subpart_folder: Path = custom.SUBPART_FOLDER,
    ):
        super().__init__()
        ImageStore([])  # applies the memory limit the largest tiles need
        self.source_folder = Path(source_folder)
        self.subpart_folder = Path(subpart_folder)
        self.current: Path | None = None
        self.setWindowTitle("Subpart Cutter")

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tiles")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh_list)
        self.tile_list = QListWidget()
        self.tile_list.setUniformItemSizes(True)
        self.tile_list.currentTextChanged.connect(self._tile_chosen)
        self.count_label = QLabel()

        left = QWidget()
        left.setFixedWidth(320)
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.tile_list, 1)
        left_layout.addWidget(self.count_label)

        self.view = CutterView()
        self.view.selection_changed.connect(self._selection_changed)

        self.snap_combo = QComboBox()
        for label, step in SNAP_CHOICES:
            self.snap_combo.addItem(label, step)
        self.snap_combo.currentIndexChanged.connect(self._snap_chosen)
        self.size_label = QLabel("Drag a box over the tile")
        self.words_edit = QLineEdit()
        self.words_edit.setPlaceholderText("Extra words (optional)")
        self.words_edit.textChanged.connect(lambda _text: self._selection_changed())
        self.name_label = QLabel()
        self.name_label.setWordWrap(True)
        self.category_list = QListWidget()
        for usage in model.USAGE_FILTERS:
            item = QListWidgetItem(usage)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.category_list.addItem(item)
        self.save_button = QPushButton("Save subpart")
        self.save_button.clicked.connect(lambda: self.save_subpart())
        self.fit_button = QPushButton("Fit to window")
        self.fit_button.clicked.connect(lambda: self.view.fit())

        form = QFormLayout()
        form.addRow("Snap to", self.snap_combo)
        form.addRow("Selection", self.size_label)
        form.addRow("Add to name", self.words_edit)
        form.addRow("Saves as", self.name_label)
        right = QWidget()
        right.setFixedWidth(300)
        right_layout = QVBoxLayout(right)
        right_layout.addLayout(form)
        right_layout.addWidget(QLabel("Categories"))
        right_layout.addWidget(self.category_list, 1)
        right_layout.addWidget(self.save_button)
        right_layout.addWidget(self.fit_button)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(left)
        layout.addWidget(self.view, 1)
        layout.addWidget(right)
        self.setCentralWidget(central)

        self.tiles = self._find_tiles()
        self.resize(1400, 860)
        self._refresh_list()
        self._selection_changed()
        if not self.tiles:
            self.statusBar().showMessage(
                f"No images found in {self.source_folder}. "
                "Run tools/fetch_geomorphs.py --high-res first."
            )

    def _find_tiles(self) -> list[str]:
        if not self.source_folder.is_dir():
            return []
        return sorted(
            path.relative_to(self.source_folder).as_posix()
            for path in self.source_folder.rglob("*.png")
        )

    def _refresh_list(self) -> None:
        text = self.search.text().strip().casefold()
        found = [tile for tile in self.tiles if text in tile.casefold()]
        self.tile_list.clear()
        self.tile_list.addItems(found)
        self.count_label.setText(f"{len(found)} of {len(self.tiles)} tiles")

    def _tile_chosen(self, tile: str) -> None:
        if not tile:
            return
        self.current = self.source_folder / tile
        QApplication.setOverrideCursor(Qt.WaitCursor)
        pixmap = QPixmap(str(self.current))
        QApplication.restoreOverrideCursor()
        if pixmap.isNull():
            self.current = None
            self.view.show_image(None)
            self.statusBar().showMessage(f"Could not open {tile}")
            return
        self.view.show_image(pixmap)

    def _snap_chosen(self, index: int) -> None:
        self.view.step = self.snap_combo.itemData(index)

    def _selection_changed(self) -> None:
        box = self.view.selection()
        has_box = not box.isEmpty()
        self.save_button.setEnabled(has_box)
        if has_box:
            width, height = self._squares(box)
            self.size_label.setText(f"{width} × {height} squares")
            self.name_label.setText(self._unique_name(self._name(box)))
        else:
            self.size_label.setText("Drag a box over the tile")
            self.name_label.clear()

    @staticmethod
    def _squares(box: QRect) -> tuple[str, str]:
        """The box size in squares, in the plainest form that's accurate."""
        return (
            f"{round(box.width() / SQUARE, 2):g}",
            f"{round(box.height() / SQUARE, 2):g}",
        )

    def _name(self, box: QRect) -> str:
        """The size, plus any words the user added."""
        width, height = self._squares(box)
        words = custom.safe_name(self.words_edit.text())
        return f"{width}x{height} {words}".strip()

    def _unique_name(self, name: str) -> str:
        """Add a number if the name is taken, so nothing gets overwritten."""
        if not custom.subpart_path(name, self.subpart_folder).exists():
            return name
        number = 2
        while custom.subpart_path(f"{name} {number}", self.subpart_folder).exists():
            number += 1
        return f"{name} {number}"

    def categories(self) -> list[str]:
        """The ticked categories, in the order the main app lists them."""
        return [
            self.category_list.item(row).text()
            for row in range(self.category_list.count())
            if self.category_list.item(row).checkState() == Qt.Checked
        ]

    def _clear_categories(self) -> None:
        for row in range(self.category_list.count()):
            self.category_list.item(row).setCheckState(Qt.Unchecked)

    def save_subpart(self) -> str | None:
        box = self.view.selection()
        if box.isEmpty() or self.view.pixmap is None:
            return None

        name = self._unique_name(self._name(box))
        path = custom.subpart_path(name, self.subpart_folder)
        self.subpart_folder.mkdir(parents=True, exist_ok=True)
        if not self.view.pixmap.copy(box).save(str(path), "PNG"):
            QMessageBox.warning(self, self.windowTitle(), "Could not save the subpart.")
            return None
        custom.save_subpart_record(
            name,
            {
                "source": self.current.relative_to(self.source_folder).as_posix(),
                "x": box.x(),
                "y": box.y(),
                "width": box.width(),
                "height": box.height(),
                "categories": self.categories(),
                "licence": "CC BY-NC 4.0, Robert Pearce and Eric B. Smith",
            },
            self.subpart_folder,
        )
        self.statusBar().showMessage(f"Saved {name}", 5000)
        self.words_edit.clear()
        self._clear_categories()
        self.view.clear_selection()
        self.saved.emit(name)
        return name


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Subpart Cutter")
    window = CutterWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
