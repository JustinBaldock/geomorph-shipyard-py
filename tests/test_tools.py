"""The Subpart Cutter, the Component Editor and how they join up."""

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QDropEvent, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QToolBar

from shipyard import custom, model
from shipyard.app import DATA_DIR, MainWindow
from shipyard.cutter import CutterWindow, snapped
from shipyard.editor import (
    DOOR_GAP,
    HULL_WALL,
    INTERIOR_WALL,
    SUBPART_MIME,
    BorderItem,
    ComponentEditor,
    DoorItem,
    canvas_size,
    compose,
    door_slot,
)
from shipyard.images import HighResStore, ImageStore

SQUARE = 300
NO_FOLDER = Path("/nonexistent/geomorphs")


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def solid(path, width, height, colour, alpha=255):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, height, QImage.Format_ARGB32)
    colour = QColor(colour)
    colour.setAlpha(alpha)
    image.fill(colour)
    assert image.save(str(path))


def quarters(path, size=1200):
    """Red, green, blue and yellow corners, to show which way up things are."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(size, size, QImage.Format_ARGB32)
    painter = QPainter(image)
    half = size // 2
    painter.fillRect(0, 0, half, half, QColor("red"))
    painter.fillRect(half, 0, half, half, QColor("green"))
    painter.fillRect(0, half, half, half, QColor("blue"))
    painter.fillRect(half, half, half, half, QColor("yellow"))
    painter.end()
    assert image.save(str(path))


@pytest.fixture
def folders(tmp_path):
    source = tmp_path / "highres"
    quarters(source / "SS" / "SS-001 [Port] [50x50] Fuel.png")
    solid(source / "HG" / "HG-002 Cargo.png", 900, 600, "blue")
    return source, tmp_path / "subparts", tmp_path / "custom"


@pytest.fixture
def cutter(app, folders):
    source, subparts, _ = folders
    window = CutterWindow(source, subparts)
    window.show()
    window.tile_list.setCurrentRow(1)
    QApplication.processEvents()
    yield window
    window.close()


def view_point(view, x, y):
    return view.mapFromScene(QPointF(x, y))


def drag_box(cutter, start, end):
    view = cutter.view
    viewport = view.viewport()
    first = view_point(view, *start)
    last = view_point(view, *end)
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, first)
    QTest.mouseMove(viewport, last)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, last)
    QApplication.processEvents()


# The cutter


def test_cutter_lists_every_tile_in_the_folder(cutter):
    assert cutter.tile_list.count() == 2
    assert "2 of 2" in cutter.count_label.text()


def test_cutter_search_narrows_the_list(cutter):
    cutter.search.setText("cargo")
    assert cutter.tile_list.count() == 1
    assert "HG-002" in cutter.tile_list.item(0).text()


def test_cutter_says_when_the_images_are_missing(app, tmp_path):
    window = CutterWindow(tmp_path / "none", tmp_path / "subparts")
    assert "fetch_geomorphs" in window.statusBar().currentMessage()
    window.close()


@pytest.mark.parametrize(
    "value, step, limit, expected",
    [(290, 300, 1200, 300), (140, 300, 1200, 0), (160, 300, 1200, 300),
     (1250, 300, 1199, 1199), (-40, 300, 1200, 0), (70, 150, 1200, 0),
     (80, 150, 1200, 150), (123, 1, 1200, 123)],
)
def test_box_edges_snap_and_stay_inside(value, step, limit, expected):
    assert snapped(value, step, limit) == expected


def test_dragging_marks_a_snapped_box(cutter):
    drag_box(cutter, (290, 310), (905, 610))
    assert cutter.view.selection() == QRect(300, 300, 600, 300)
    assert cutter.size_label.text() == "2 × 1 squares"
    assert cutter.save_button.isEnabled()


def test_dragging_backwards_works_too(cutter):
    drag_box(cutter, (905, 610), (290, 310))
    assert cutter.view.selection() == QRect(300, 300, 600, 300)


def test_a_click_with_no_drag_marks_nothing(cutter):
    drag_box(cutter, (300, 300), (300, 300))
    assert cutter.view.selection().isEmpty()
    assert not cutter.save_button.isEnabled()


def test_finer_snapping_is_available(cutter):
    cutter.snap_combo.setCurrentIndex(1)
    drag_box(cutter, (140, 160), (460, 440))
    assert cutter.view.selection() == QRect(150, 150, 300, 300)


def test_saving_writes_the_exact_pixels_and_a_record(cutter, folders):
    _, subparts, _ = folders
    drag_box(cutter, (0, 0), (600, 600))
    assert cutter.save_subpart() == "2x2"

    image = QImage(str(subparts / "2x2.png"))
    assert (image.width(), image.height()) == (600, 600)
    assert image.pixelColor(10, 10) == QColor("red")
    record = json.loads((subparts / "2x2.json").read_text())
    assert record["source"].endswith("SS-001 [Port] [50x50] Fuel.png")
    assert (record["x"], record["y"], record["width"]) == (0, 0, 600)
    assert "CC BY-NC" in record["licence"]


def test_the_name_defaults_to_the_size(cutter):
    drag_box(cutter, (300, 600), (900, 900))
    assert cutter.name_label.text() == "2x1"


def test_half_squares_show_in_the_name(cutter):
    cutter.snap_combo.setCurrentIndex(1)
    drag_box(cutter, (0, 0), (750, 150))
    assert cutter.name_label.text() == "2.5x0.5"
    assert cutter.save_subpart() == "2.5x0.5"
    assert custom.subpart_names(cutter.subpart_folder) == ["2.5x0.5"]


def test_extra_words_follow_the_size(cutter, folders):
    drag_box(cutter, (0, 0), (900, 600))
    cutter.words_edit.setText("Medical bay")
    assert cutter.name_label.text() == "3x2 Medical bay"
    assert cutter.save_subpart() == "3x2 Medical bay"
    assert (folders[1] / "3x2 Medical bay.png").exists()


def test_words_can_be_typed_before_the_box_is_drawn(cutter):
    cutter.words_edit.setText("Airlock")
    drag_box(cutter, (0, 0), (300, 300))
    assert cutter.name_label.text() == "1x1 Airlock"


def test_a_taken_name_gets_a_number_and_overwrites_nothing(cutter, folders):
    drag_box(cutter, (0, 0), (300, 300))
    assert cutter.save_subpart() == "1x1"
    drag_box(cutter, (600, 600), (900, 900))
    assert cutter.name_label.text() == "1x1 2"
    assert cutter.save_subpart() == "1x1 2"
    drag_box(cutter, (0, 600), (300, 900))
    assert cutter.save_subpart() == "1x1 3"
    assert QImage(str(folders[1] / "1x1.png")).pixelColor(10, 10) == QColor("red")
    assert QImage(str(folders[1] / "1x1 2.png")).pixelColor(10, 10) == QColor("yellow")


def test_the_box_and_words_clear_after_saving(cutter):
    drag_box(cutter, (0, 0), (300, 300))
    cutter.words_edit.setText("Room")
    cutter.save_subpart()
    assert cutter.view.selection().isEmpty()
    assert cutter.words_edit.text() == ""
    assert cutter.name_label.text() == ""


def test_hostile_words_stay_in_the_folder(cutter, folders):
    drag_box(cutter, (0, 0), (300, 300))
    cutter.words_edit.setText("../../evil")
    assert cutter.save_subpart() == "1x1 evil"
    assert (folders[1] / "1x1 evil.png").exists()


def tick_category(cutter, label):
    item = cutter.category_list.findItems(label, Qt.MatchExactly)[0]
    item.setCheckState(Qt.Checked)


def test_categories_match_the_main_app_filters(cutter):
    labels = [
        cutter.category_list.item(row).text()
        for row in range(cutter.category_list.count())
    ]
    assert labels == list(model.USAGE_FILTERS)


def test_categories_start_unticked_and_can_be_ticked(cutter):
    assert cutter.categories() == []
    item = cutter.category_list.item(0)
    assert item.flags() & Qt.ItemIsUserCheckable
    tick_category(cutter, "Fuel")
    assert cutter.categories() == ["Fuel"]


def test_categories_are_saved_with_the_source(cutter, folders):
    drag_box(cutter, (0, 0), (300, 300))
    tick_category(cutter, "Fuel")
    tick_category(cutter, "Engineering")
    cutter.save_subpart()
    record = json.loads((folders[1] / "1x1.json").read_text())
    assert record["categories"] == ["Engineering", "Fuel"]
    assert record["source"].endswith("Fuel.png")


def test_no_categories_saves_an_empty_list(cutter, folders):
    drag_box(cutter, (0, 0), (300, 300))
    cutter.save_subpart()
    assert json.loads((folders[1] / "1x1.json").read_text())["categories"] == []


def test_ticks_clear_after_saving(cutter):
    drag_box(cutter, (0, 0), (300, 300))
    tick_category(cutter, "Fuel")
    cutter.save_subpart()
    assert cutter.categories() == []


# The editor


@pytest.fixture
def library():
    return model.load_library(DATA_DIR)


@pytest.fixture
def editor(app, folders, library):
    _, subparts, custom_folder = folders
    quarters(subparts / "Room.png", 600)
    solid(subparts / "Wall.png", 300, 150, "blue")
    window = ComponentEditor(library, subparts, custom_folder)
    window.show()
    QApplication.processEvents()
    yield window
    window.dirty = False
    window.close()


def drop(editor, name, x, y):
    data = QMimeData()
    data.setData(SUBPART_MIME, name.encode("utf-8"))
    point = QPointF(editor.view.mapFromScene(QPointF(x, y)))
    event = QDropEvent(point, Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
    editor.view.dropEvent(event)
    QApplication.processEvents()


def test_editor_lists_subparts_with_thumbnails(editor):
    assert editor.subpart_list.count() == 2
    assert not editor.subpart_list.item(0).icon().isNull()


def test_editor_search_hides_subparts(editor):
    editor.search.setText("wall")
    assert editor.subpart_count.text() == "1 of 2 subparts"


def test_editor_search_finds_categories(app, folders, library):
    _, subparts, custom_folder = folders
    quarters(subparts / "3x2.png", 600)
    custom.save_subpart_record("3x2", {"categories": ["Fuel", "Cargo"]}, subparts)
    solid(subparts / "1x1.png", 300, 300, "blue")
    window = ComponentEditor(library, subparts, custom_folder)
    window.search.setText("fuel")
    assert window.subpart_count.text() == "1 of 2 subparts"
    found = window.subpart_list.findItems("3x2", Qt.MatchExactly)[0]
    assert not found.isHidden()
    assert found.toolTip() == "Fuel, Cargo"
    window.dirty = False
    window.close()


def test_subparts_without_categories_still_work(tmp_path):
    assert custom.subpart_categories("none", tmp_path) == []
    (tmp_path / "old.json").write_text('{"source": "x.png"}')
    assert custom.subpart_categories("old", tmp_path) == []
    (tmp_path / "bad.json").write_text("not json")
    assert custom.subpart_categories("bad", tmp_path) == []


def test_the_list_offers_the_subpart_for_dragging(editor):
    item = editor.subpart_list.item(0)
    data = editor.subpart_list.mimeData([item])
    assert bytes(data.data(SUBPART_MIME)).decode() == item.text()


def test_the_canvas_accepts_only_subparts(editor):
    other = QMimeData()
    other.setText("something else")
    event = QDropEvent(QPointF(5, 5), Qt.CopyAction, other, Qt.LeftButton, Qt.NoModifier)
    editor.view.dropEvent(event)
    assert editor.items() == []


def test_dropping_places_a_snapped_subpart(editor):
    drop(editor, "Room", 1120, 1190)
    item = editor.items()[0]
    assert item.pos() == QPointF(900, 900)
    assert item.isSelected()
    assert editor.dirty


def test_dragging_a_subpart_snaps_it(editor):
    drop(editor, "Room", 1200, 1200)
    item = editor.items()[0]
    viewport = editor.view.viewport()
    start = editor.view.mapFromScene(QPointF(1200, 1200))
    end = editor.view.mapFromScene(QPointF(1610, 1500))
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(viewport, (start + end) / 2)
    QTest.mouseMove(viewport, end)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
    QApplication.processEvents()
    assert item.pos().x() % SQUARE == 0 and item.pos().y() % SQUARE == 0
    assert item.pos() != QPointF(900, 900)


def test_half_square_snapping(editor):
    editor.snap_combo.setCurrentIndex(1)
    drop(editor, "Wall", 1250, 1200)
    item = editor.items()[0]
    assert item.pos().x() % 150 == 0 and item.pos().y() % 150 == 0


def composed(editor):
    size = canvas_size(editor.width_spin.value(), editor.height_spin.value())
    return compose(editor.items(), size)


@pytest.mark.parametrize(
    "turns, mirror, top_left, top_right, bottom_left",
    [
        (0, False, "red", "green", "blue"),
        (1, False, "blue", "red", "yellow"),  # a quarter turn clockwise
        (2, False, "yellow", "blue", "green"),
        (3, False, "green", "yellow", "red"),
        (0, True, "green", "red", "yellow"),  # flipped left to right
    ],
)
def test_turning_and_flipping_land_correctly(
    editor, turns, mirror, top_left, top_right, bottom_left
):
    drop(editor, "Room", 900, 900)
    item = editor.items()[0]
    item.set_pose(turns, mirror)
    assert item.pos() == QPointF(600, 600)

    image = composed(editor)
    assert image.pixelColor(610, 610) == QColor(top_left)
    assert image.pixelColor(1190, 610) == QColor(top_right)
    assert image.pixelColor(610, 1190) == QColor(bottom_left)


def test_rotate_keeps_a_long_subpart_on_the_grid(editor):
    drop(editor, "Wall", 900, 900)
    item = editor.items()[0]
    corner = item.pos()
    editor.turn_selected(1)
    assert item.pos() == corner
    drawn = item.transform().mapRect(item.pixmap().rect())
    assert drawn.topLeft() == QPoint(0, 0)
    assert (drawn.width(), drawn.height()) == (150, 300)


def test_layer_order_controls_what_is_on_top(editor):
    drop(editor, "Room", 900, 900)
    room = editor.items()[0]
    drop(editor, "Wall", 750, 675)
    wall = next(item for item in editor.items() if item is not room)
    wall.setPos(600, 600)
    assert composed(editor).pixelColor(610, 610) == QColor("blue")

    editor.scene.clearSelection()
    room.setSelected(True)
    editor.bring_to_front()
    assert composed(editor).pixelColor(610, 610) == QColor("red")

    editor.send_to_back()
    assert composed(editor).pixelColor(610, 610) == QColor("blue")


def test_the_background_stays_transparent(editor):
    drop(editor, "Wall", 900, 900)
    image = composed(editor)
    assert image.pixelColor(5, 5).alpha() == 0


def test_the_canvas_matches_real_tile_proportions(editor):
    editor.width_spin.setValue(10)
    editor.height_spin.setValue(20)
    assert canvas_size(10, 20) == QSize(14 * SQUARE, 24 * SQUARE)
    assert editor.scene.part_rect().topLeft() == QPointF(600, 600)
    assert editor.scene.part_rect().width() == 10 * SQUARE


def test_delete_removes_the_selected_subpart(editor):
    drop(editor, "Room", 900, 900)
    editor.delete_selected()
    assert editor.items() == []


def test_tons_default_to_the_area(editor):
    editor.width_spin.setValue(10)
    editor.height_spin.setValue(4)
    assert editor._tons() == 20
    assert "area" in editor.tons_label.text()


def test_tons_come_from_the_uses_when_set(editor):
    editor.use_spins["Fuel"].setValue(8)
    editor.use_spins["Cargo"].setValue(2)
    assert editor._tons() == 10
    assert editor._uses() == {"Cargo": 2, "Fuel": 8}


def test_saving_needs_a_code_and_a_subpart(editor, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    assert editor.save_component() is None
    assert "subpart" in shown[0]
    drop(editor, "Room", 900, 900)
    editor.code_edit.setText("  ")
    assert editor.save_component() is None
    assert "code" in shown[1]


def test_a_library_code_cannot_be_taken(editor, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    drop(editor, "Room", 900, 900)
    editor.code_edit.setText("SS-001")
    assert editor.save_component() is None
    assert "already" in shown[0]


def save_part(editor, code="CU-001"):
    drop(editor, "Room", 1500, 1500)
    editor.code_edit.setText(code)
    editor.use_spins["Bridge"].setValue(30)
    assert editor.save_component() == code


def test_saving_writes_both_image_sizes_and_the_entry(editor, folders):
    custom_folder = folders[2]
    save_part(editor)

    high, low = custom.image_paths("CU-001", custom_folder)
    high_size = QImage(str(high)).size()
    low_size = QImage(str(low)).size()
    assert high_size == QSize(14 * SQUARE, 14 * SQUARE)
    assert low_size == QSize(840, 840)

    entry = custom.load_entries(custom_folder)["CU-001"]
    assert entry["url"] == "CU-001.png"
    assert (entry["sizeX"], entry["sizeY"], entry["tons"]) == (10, 10, 30)
    assert entry["uses"] == {"Bridge": 30}
    assert entry["layout"][0]["subpart"] == "Room"
    assert not editor.dirty


def test_a_saved_part_loads_into_the_library(editor, folders):
    save_part(editor)
    entries = custom.load_entries(folders[2])
    library = model.load_library(DATA_DIR, entries)
    info = library["CU-001"]
    assert info.shape == "Custom"
    assert info.tons == 30
    placement = model.place(model.Part("CU-001", (0, 0)), info)
    assert placement.bounds == (0, 0, 50, 50)
    assert model.filter_library(library, shapes={"Custom"}) == [info]


def test_new_codes_skip_ones_in_use(editor):
    save_part(editor)
    editor.new_component()
    assert editor.code_edit.text() == "CU-002"
    assert editor.items() == []


def test_a_saved_part_reopens_exactly(editor):
    drop(editor, "Room", 900, 900)
    drop(editor, "Wall", 1500, 1500)
    room = next(item for item in editor.items() if item.name == "Room")
    room.set_pose(1, True)
    editor.code_edit.setText("CU-005")
    editor.use_spins["Fuel"].setValue(12)
    editor.width_spin.setValue(12)
    assert editor.save_component() == "CU-005"
    before = composed(editor)

    editor.new_component()
    assert editor.open_component("CU-005")
    assert editor.code_edit.text() == "CU-005"
    assert editor.width_spin.value() == 12
    assert editor.use_spins["Fuel"].value() == 12
    assert len(editor.items()) == 2
    assert composed(editor) == before
    assert not editor.dirty


def test_saving_over_a_part_asks_first(editor, monkeypatch):
    save_part(editor)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Cancel)
    assert editor.save_component() is None


def test_reopening_names_missing_subparts(editor, monkeypatch, folders):
    save_part(editor)
    (folders[1] / "Room.png").unlink()
    editor.dirty = False
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    assert editor.open_component("CU-001")
    assert "Room" in shown[0]


def test_a_broken_part_file_is_named_and_loses_only_itself(editor, folders):
    save_part(editor, "CU-050")
    (folders[2] / "CU-051.json").write_text("not json")
    assert list(editor._custom_entries()) == ["CU-050"]
    assert "CU-051.json" in editor.statusBar().currentMessage()


def test_closing_with_unsaved_work_asks(editor, monkeypatch):
    drop(editor, "Room", 900, 900)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Cancel)
    assert not editor.close()
    assert editor.isVisible()


def click_piece(editor, x, y, modifier=Qt.NoModifier):
    QTest.mouseClick(
        editor.view.viewport(),
        Qt.LeftButton,
        modifier,
        editor.view.mapFromScene(QPointF(x, y)),
    )
    QApplication.processEvents()


def two_pieces(editor):
    drop(editor, "Room", 900, 900)  # top left at 600, 600
    drop(editor, "Wall", 2250, 2175)  # top left at 2100, 2100
    return (
        next(item for item in editor.items() if item.name == "Room"),
        next(item for item in editor.items() if item.name == "Wall"),
    )


def test_copy_needs_a_selection_and_paste_needs_a_copy(editor):
    assert not editor.copy_action.isEnabled()
    assert not editor.paste_action.isEnabled()
    drop(editor, "Room", 900, 900)
    assert editor.copy_action.isEnabled()
    assert not editor.paste_action.isEnabled()
    editor.copy_action.trigger()
    assert editor.paste_action.isEnabled()


def test_copy_and_paste_are_on_the_toolbar(editor):
    bar = editor.findChild(QToolBar)
    assert editor.copy_action in bar.actions()
    assert editor.paste_action in bar.actions()


def test_paste_adds_a_copy_a_square_away(editor):
    drop(editor, "Room", 900, 900)
    original = editor.items()[0]
    editor.copy_selected()
    editor.paste()

    assert len(editor.items()) == 2
    copy = next(item for item in editor.items() if item is not original)
    assert copy.pos() == original.pos() + QPointF(SQUARE, SQUARE)
    assert copy.name == "Room"
    assert editor.selected() == [copy]
    assert editor.dirty


def test_a_copy_keeps_its_rotation_and_flip(editor):
    drop(editor, "Room", 900, 900)
    original = editor.items()[0]
    original.set_pose(1, True)
    editor.copy_selected()
    editor.paste()
    copy = next(item for item in editor.items() if item is not original)
    assert (copy.turns, copy.mirror) == (1, True)
    assert copy.transform() == original.transform()


def test_each_paste_steps_along_a_square(editor):
    drop(editor, "Room", 900, 900)
    editor.copy_selected()
    editor.paste()
    editor.paste()
    editor.paste()
    spots = sorted(item.pos().x() for item in editor.items())
    assert spots == [600, 900, 1200, 1500]


def test_a_new_copy_starts_the_steps_again(editor):
    drop(editor, "Room", 900, 900)
    editor.copy_selected()
    editor.paste()
    editor.paste()
    editor.copy_selected()
    latest = editor.selected()[0]
    editor.paste()
    assert editor.selected()[0].pos() == latest.pos() + QPointF(SQUARE, SQUARE)


def test_a_copy_lands_on_top(editor):
    drop(editor, "Room", 900, 900)
    original = editor.items()[0]
    editor.copy_selected()
    editor.paste()
    assert editor.selected()[0].zValue() > original.zValue()


def test_a_group_copies_with_its_layout_and_layers(editor):
    room, wall = two_pieces(editor)
    room.setSelected(True)
    wall.setSelected(True)
    editor.copy_selected()
    editor.paste()

    copies = sorted(editor.selected(), key=lambda item: item.zValue())
    assert [item.name for item in copies] == ["Room", "Wall"]
    gap = copies[1].pos() - copies[0].pos()
    assert gap == wall.pos() - room.pos()


def test_cut_removes_and_paste_brings_it_back_nearby(editor):
    drop(editor, "Room", 900, 900)
    editor.cut_selected()
    assert editor.items() == []
    assert editor.paste_action.isEnabled()
    editor.paste()
    assert editor.items()[0].pos() == QPointF(900, 900)


def test_changing_a_copy_leaves_the_original_alone(editor):
    drop(editor, "Room", 900, 900)
    original = editor.items()[0]
    editor.copy_selected()
    editor.paste()
    editor.turn_selected(1)
    assert original.turns == 0


def test_the_copy_survives_a_new_component(editor):
    drop(editor, "Room", 900, 900)
    editor.copy_selected()
    editor.dirty = False
    editor.new_component()
    assert editor.items() == []
    editor.paste()
    assert editor.items()[0].name == "Room"


def test_pasted_pieces_save_and_reopen(editor):
    drop(editor, "Room", 1500, 1500)
    editor.copy_selected()
    editor.paste()
    editor.code_edit.setText("CU-020")
    assert editor.save_component() == "CU-020"
    before = composed(editor)
    editor.new_component()
    assert editor.open_component("CU-020")
    assert len(editor.items()) == 2
    assert composed(editor) == before


def test_shift_click_adds_a_piece_to_the_selection(editor):
    room, wall = two_pieces(editor)
    editor.scene.clearSelection()
    click_piece(editor, 900, 900)
    assert editor.selected() == [room]
    click_piece(editor, 2250, 2175, Qt.ShiftModifier)
    assert set(editor.selected()) == {room, wall}
    click_piece(editor, 900, 900, Qt.ShiftModifier)
    assert editor.selected() == [wall]


def test_select_all_takes_every_piece(editor):
    room, wall = two_pieces(editor)
    editor.scene.clearSelection()
    editor.select_all()
    assert set(editor.selected()) == {room, wall}


def test_dragging_a_group_keeps_it_together(editor):
    room, wall = two_pieces(editor)
    gap = wall.pos() - room.pos()
    editor.select_all()
    viewport = editor.view.viewport()
    start = editor.view.mapFromScene(QPointF(900, 900))
    end = editor.view.mapFromScene(QPointF(1500, 1500))
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(viewport, (start + end) / 2)
    QTest.mouseMove(viewport, end)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
    QApplication.processEvents()
    assert room.pos() != QPointF(600, 600)
    assert wall.pos() - room.pos() == gap


# One JSON file per part


def entry(url="A.png", **changes):
    return {
        "url": url, "sizeX": 10, "sizeY": 10, "tons": 50, "uses": {},
        "facilities": {}, **changes,
    }


def part_on_disk(folder, code):
    solid(folder / f"{code}.png", 20, 20, "red")
    custom.save_entry(code, entry(f"{code}.png"), folder)


def test_each_saved_part_gets_its_own_json_file(editor, folders):
    save_part(editor, "CU-060")
    editor.new_component()
    save_part(editor, "CU-061")
    names = sorted(path.name for path in folders[2].glob("*.json"))
    assert names == ["CU-060.json", "CU-061.json"]
    saved = json.loads((folders[2] / "CU-060.json").read_text())
    assert saved["url"] == "CU-060.png"
    assert saved["uses"] == {"Bridge": 30}


def test_a_part_with_spaces_in_its_code_saves_and_reopens(editor, folders):
    save_part(editor, "CU-002-20x10-Cargo Bay-6xHalf Containers")
    assert (folders[2] / "CU-002-20x10-Cargo Bay-6xHalf Containers.json").exists()
    editor.new_component()
    assert editor.open_component("CU-002-20x10-Cargo Bay-6xHalf Containers")
    assert editor.items()[0].name == "Room"


def test_editing_a_part_changes_only_its_own_file(editor, folders, monkeypatch):
    save_part(editor, "CU-062")
    editor.new_component()
    save_part(editor, "CU-063")
    untouched = (folders[2] / "CU-063.json").read_bytes()

    assert editor.open_component("CU-062")
    editor.use_spins["Bridge"].setValue(40)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    assert editor.save_component() == "CU-062"

    assert json.loads((folders[2] / "CU-062.json").read_text())["tons"] == 40
    assert (folders[2] / "CU-063.json").read_bytes() == untouched


def test_deleting_a_parts_files_removes_only_that_part(tmp_path):
    part_on_disk(tmp_path, "CU-001")
    part_on_disk(tmp_path, "CU-002")
    (tmp_path / "CU-001.png").unlink()
    (tmp_path / "CU-001.json").unlink()
    assert list(custom.load_entries(tmp_path)) == ["CU-002"]


def test_a_json_with_no_high_res_image_is_ignored(tmp_path):
    part_on_disk(tmp_path, "CU-001")
    (tmp_path / "CU-001.png").unlink()
    assert custom.load_entries(tmp_path) == {}


def test_a_leftover_low_res_image_does_not_keep_a_part(tmp_path):
    part_on_disk(tmp_path, "CU-001")
    solid(tmp_path / "lowres" / "CU-001.png", 20, 20, "red")
    (tmp_path / "CU-001.png").unlink()
    assert custom.load_entries(tmp_path) == {}


def test_the_old_shared_file_is_ignored(tmp_path):
    part_on_disk(tmp_path, "CU-001")
    (tmp_path / custom.OLD_PARTS_FILE_NAME).write_text(
        json.dumps({"CU-OLD": entry("gone.png")})
    )
    problems = []
    assert list(custom.load_entries(tmp_path, problems)) == ["CU-001"]
    assert problems == []


@pytest.mark.parametrize("text", ["not json", "[]", '"text"', '{"url": "A.png"}'])
def test_a_damaged_file_is_reported_and_skipped(tmp_path, text):
    part_on_disk(tmp_path, "CU-001")
    (tmp_path / "CU-002.json").write_text(text)
    problems = []
    assert list(custom.load_entries(tmp_path, problems)) == ["CU-001"]
    assert problems == ["CU-002.json"]


def test_no_folder_means_no_parts(tmp_path):
    assert custom.load_entries(tmp_path / "missing") == {}


def test_a_saved_part_does_not_block_reusing_its_code_after_deletion(editor, folders):
    save_part(editor, "CU-070")
    editor.library["CU-070"] = model.part_from_entry(
        "CU-070", "Custom", custom.load_entries(folders[2])["CU-070"]
    )
    (folders[2] / "CU-070.png").unlink()
    (folders[2] / "CU-070.json").unlink()
    editor.new_component()
    save_part(editor, "CU-070")
    assert (folders[2] / "CU-070.json").exists()
    del editor.library["CU-070"]


def test_a_built_in_code_is_still_refused(editor, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    drop(editor, "Room", 900, 900)
    editor.code_edit.setText("SS-001")
    assert editor.save_component() is None
    assert shown


def test_the_saved_list_catches_up_with_deleted_files(editor, folders):
    save_part(editor, "CU-071")
    assert editor.saved_combo.findText("CU-071") > 0
    (folders[2] / "CU-071.png").unlink()
    editor._refresh_saved_parts()
    assert editor.saved_combo.findText("CU-071") < 0


def only(editor, kind):
    return [item for item in editor.items() if isinstance(item, kind)]


def scene_click(editor, x, y, modifier=Qt.NoModifier):
    point = editor.view.mapFromScene(QPointF(x, y))
    QTest.mouseClick(editor.view.viewport(), Qt.LeftButton, modifier, point)
    QApplication.processEvents()


def paint_stroke(editor, points, modifier=Qt.NoModifier):
    viewport = editor.view.viewport()
    spots = [editor.view.mapFromScene(QPointF(*point)) for point in points]
    QTest.mousePress(viewport, Qt.LeftButton, modifier, spots[0])
    for spot in spots[1:]:
        QTest.mouseMove(viewport, spot)
    QTest.mouseRelease(viewport, Qt.LeftButton, modifier, spots[-1])
    QApplication.processEvents()


def dark(image, x, y):
    colour = image.pixelColor(x, y)
    return colour.alpha() > 200 and colour.red() < 80


# Borders


def test_draw_border_goes_around_the_whole_part(editor):
    editor.width_spin.setValue(10)
    editor.height_spin.setValue(6)
    border = editor.draw_border()
    assert border.area() == editor.scene.part_rect()
    assert border.follows_part
    assert border.thickness == HULL_WALL
    assert editor.dirty


def test_the_border_draws_real_hull_thickness_on_the_edge(editor):
    editor.draw_border()
    image = composed(editor)
    # The wall is centred on the part's edge, at x = 600 here.
    assert dark(image, 600, 1500)
    assert dark(image, 600 - 18, 1500) and dark(image, 600 + 18, 1500)
    assert not dark(image, 600 - 22, 1500) and not dark(image, 600 + 22, 1500)


def test_the_interior_thickness_can_be_chosen(editor):
    editor.border_combo.setCurrentIndex(1)
    assert editor.draw_border().thickness == INTERIOR_WALL


def test_a_border_goes_around_the_selected_pieces(editor):
    room, wall = two_pieces(editor)
    editor.scene.clearSelection()
    room.setSelected(True)
    border = editor.draw_border()
    assert border.area() == QRectF(600, 600, 600, 600)
    assert not border.follows_part


def test_a_border_covers_every_selected_piece(editor):
    room, wall = two_pieces(editor)
    editor.select_all()
    # The wall piece ends at 2250, so the border snaps out to the next square.
    assert editor.draw_border().area() == QRectF(600, 600, 1800, 1800)


def test_a_turned_piece_gets_a_border_around_what_you_see(editor):
    editor.snap_combo.setCurrentIndex(1)
    drop(editor, "Wall", 1200, 1200)
    item = editor.items()[0]
    item.set_pose(1, False)
    assert editor.draw_border().area().size().toSize() == QSize(150, 300)


def test_a_thin_piece_never_collapses_its_border(editor):
    drop(editor, "Wall", 1200, 1200)
    item = editor.items()[0]
    item.set_pose(1, False)
    area = editor.draw_border().area()
    assert area.width() >= 150 and area.height() >= 300
    assert area.contains(item.visible_rect())


def test_a_whole_part_border_follows_the_part_size(editor):
    border = editor.draw_border()
    editor.width_spin.setValue(20)
    assert border.area() == editor.scene.part_rect()


def test_a_piece_border_ignores_the_part_size(editor):
    room, _ = two_pieces(editor)
    editor.scene.clearSelection()
    room.setSelected(True)
    border = editor.draw_border()
    before = border.area()
    editor.width_spin.setValue(20)
    assert border.area() == before


def test_a_border_can_be_clicked_on_its_wall(editor):
    border = editor.draw_border()
    area = border.area()
    scene_click(editor, area.left(), area.center().y())
    assert editor.selected() == [border]


def test_clicking_inside_a_border_reaches_the_piece(editor):
    drop(editor, "Room", 1500, 1500)
    piece = editor.items()[0]
    border = editor.draw_border()
    editor.scene.clearSelection()
    scene_click(editor, 1500, 1500)
    assert editor.selected() == [piece]
    assert border not in editor.selected()


def test_shift_click_adds_a_border_to_the_selection(editor):
    drop(editor, "Room", 1500, 1500)
    piece = editor.items()[0]
    border = editor.draw_border()
    editor.scene.clearSelection()
    piece.setSelected(True)
    area = border.area()
    scene_click(editor, area.left(), area.center().y(), Qt.ShiftModifier)
    assert set(editor.selected()) == {piece, border}


def test_a_border_stays_above_pieces_added_later(editor):
    border = editor.draw_border()
    drop(editor, "Room", 900, 900)
    room = next(item for item in editor.items() if item is not border)
    assert border.zValue() > room.zValue()


def test_a_border_can_be_deleted(editor):
    border = editor.draw_border()
    border.setSelected(True)
    editor.delete_selected()
    assert editor.items() == []


def test_rotating_ignores_borders_and_doors(editor):
    border = editor.draw_border()
    door = editor._add_door(2100, 600, False)
    border.setSelected(True)
    door.setSelected(True)
    editor.turn_selected(1)
    editor.flip_selected()
    assert door.vertical is False


# Doors


@pytest.mark.parametrize(
    "x, y, expected",
    [
        (1650, 1190, (1650, 1200, False)),  # near a horizontal line
        (1190, 1650, (1200, 1650, True)),  # near a vertical line
        (1740, 1210, (1650, 1200, False)),
        (1210, 1740, (1200, 1650, True)),
        (1200, 1200, (1350, 1200, False)),  # a tie goes across
    ],
)
def test_door_slots_pick_the_nearest_wall(x, y, expected):
    assert door_slot(x, y, SQUARE) == expected


def test_finer_snapping_moves_doors_along_the_wall(editor):
    assert door_slot(1540, 1190, 150) == (1500, 1200, False)


def test_the_brush_starts_off_and_toggles(editor):
    assert not editor.door_action.isChecked()
    editor.door_action.setChecked(True)
    assert editor.view.brush_on
    assert editor.view.dragMode() == editor.view.DragMode.NoDrag
    editor.door_action.setChecked(False)
    assert not editor.view.brush_on
    assert editor.view.dragMode() == editor.view.DragMode.RubberBandDrag


def test_the_brush_is_on_the_toolbar_with_draw_border(editor):
    bar = editor.findChild(QToolBar)
    assert editor.door_action in bar.actions()
    assert editor.border_action in bar.actions()


def test_escape_turns_the_brush_off(editor):
    editor.door_action.setChecked(True)
    editor.brush_off_action.trigger()
    assert not editor.door_action.isChecked()


def test_clicking_with_the_brush_adds_a_door(editor):
    editor.door_action.setChecked(True)
    scene_click(editor, 1700, 1205)
    doors = only(editor, DoorItem)
    assert len(doors) == 1
    assert doors[0].pos() == QPointF(1650, 1200)
    assert not doors[0].vertical
    assert editor.dirty


def test_the_brush_does_not_select_or_move_pieces(editor):
    drop(editor, "Room", 1500, 1500)
    piece = editor.items()[0]
    before = piece.pos()
    editor.scene.clearSelection()
    editor.door_action.setChecked(True)
    paint_stroke(editor, [(1500, 1500), (1700, 1500)])
    assert piece.pos() == before
    assert piece not in editor.selected()


def test_dragging_paints_a_door_in_each_square(editor):
    editor.door_action.setChecked(True)
    paint_stroke(editor, [(1320, 1205), (2200, 1205)])
    spots = sorted(door.pos().x() for door in only(editor, DoorItem))
    assert spots == [1350, 1650, 1950, 2250]


def test_a_fast_drag_leaves_no_gaps(editor):
    editor.door_action.setChecked(True)
    paint_stroke(editor, [(750, 1205), (3000, 1205)])
    spots = sorted(door.pos().x() for door in only(editor, DoorItem))
    assert spots == [750 + SQUARE * step for step in range(9)]


def test_a_wobbly_stroke_stays_on_its_wall(editor):
    editor.door_action.setChecked(True)
    paint_stroke(editor, [(1350, 1210), (1500, 1340), (1950, 1190)])
    doors = only(editor, DoorItem)
    assert all(not door.vertical and door.pos().y() == 1200 for door in doors)


def test_brushing_over_a_door_adds_no_duplicate(editor):
    editor.door_action.setChecked(True)
    scene_click(editor, 1700, 1205)
    scene_click(editor, 1650, 1195)
    assert len(only(editor, DoorItem)) == 1


def test_option_click_removes_a_door(editor):
    editor.door_action.setChecked(True)
    scene_click(editor, 1700, 1205)
    scene_click(editor, 1700, 1205, Qt.AltModifier)
    assert only(editor, DoorItem) == []


def test_option_drag_erases_along_a_wall(editor):
    editor.door_action.setChecked(True)
    paint_stroke(editor, [(1320, 1205), (2200, 1205)])
    paint_stroke(editor, [(1320, 1205), (1700, 1205)], Qt.AltModifier)
    assert sorted(door.pos().x() for door in only(editor, DoorItem)) == [1950, 2250]


def test_a_door_cuts_a_gap_in_a_border(editor):
    editor.draw_border()
    area = editor.scene.part_rect()
    x = area.left() + 1050
    y = area.top()
    before = composed(editor)
    assert dark(before, int(x), int(y))

    editor._add_door(x, y, False)
    image = composed(editor)
    # The clear gap runs the full measured width, between the rounded ends.
    assert image.pixelColor(int(x) + DOOR_GAP // 2 - 4, int(y)).red() == 255
    assert image.pixelColor(int(x) - DOOR_GAP // 2 + 4, int(y)).red() == 255
    assert dark(image, int(x) + DOOR_GAP // 2 + 24, int(y))
    assert dark(image, int(x) - DOOR_GAP // 2 - 24, int(y))


def test_a_door_draws_the_tiles_outline(editor):
    editor._add_door(1650, 1200, False)
    image = composed(editor)
    assert dark(image, 1650, 1200 - 20)
    assert dark(image, 1650, 1200 + 20)
    assert image.pixelColor(1650, 1200).red() == 255


def test_a_vertical_door_runs_down_its_wall(editor):
    editor._add_door(1200, 1650, True)
    image = composed(editor)
    assert dark(image, 1200 - 20, 1650)
    assert dark(image, 1200 + 20, 1650)


def test_a_door_in_a_hull_takes_the_hull_thickness(editor):
    editor.draw_border()
    top = editor.scene.part_rect().top()
    door = editor._add_door(1050, top, False)
    assert door.wall == HULL_WALL


def test_a_door_away_from_borders_takes_the_interior_thickness(editor):
    assert editor._add_door(1650, 1200, False).wall == INTERIOR_WALL


def test_doors_stay_above_borders_and_pieces(editor):
    door = editor._add_door(1650, 1200, False)
    border = editor.draw_border()
    drop(editor, "Room", 1500, 1500)
    assert door.zValue() > border.zValue()
    assert door.zValue() > max(
        item.zValue() for item in editor.items() if item is not door
    )


def test_a_door_can_be_selected_and_deleted(editor):
    door = editor._add_door(1650, 1200, False)
    scene_click(editor, 1650, 1200)
    assert editor.selected() == [door]
    editor.delete_selected()
    assert editor.items() == []


def test_a_vertical_door_can_be_clicked_along_its_length(editor):
    door = editor._add_door(1200, 1650, True)
    scene_click(editor, 1200, 1740)
    assert editor.selected() == [door]


def test_a_door_does_not_drag(editor):
    door = editor._add_door(1650, 1200, False)
    door.setSelected(True)
    viewport = editor.view.viewport()
    start = editor.view.mapFromScene(QPointF(1650, 1200))
    end = editor.view.mapFromScene(QPointF(2200, 1500))
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(viewport, end)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
    assert door.pos() == QPointF(1650, 1200)


# Saving what was drawn


def build_with_everything(editor):
    drop(editor, "Room", 1500, 1500)
    editor.draw_border()
    editor.border_combo.setCurrentIndex(1)
    editor.scene.clearSelection()
    editor.items()[-1].setSelected(True)
    editor.draw_border()
    top = editor.scene.part_rect().top()
    editor._add_door(1050, top, False)
    editor._add_door(1200, 1650, True)


def test_borders_and_doors_save_and_reopen_exactly(editor, folders):
    build_with_everything(editor)
    editor.code_edit.setText("CU-030")
    assert editor.save_component() == "CU-030"
    before = composed(editor)
    layout = custom.load_entries(folders[2])["CU-030"]["layout"]
    assert {entry.get("kind", "subpart") for entry in layout} == {
        "subpart",
        "border",
        "door",
    }

    editor.new_component()
    assert editor.open_component("CU-030")
    assert len(only(editor, BorderItem)) == 2
    assert len(only(editor, DoorItem)) == 2
    assert composed(editor) == before
    assert not editor.dirty


def test_a_reopened_part_border_still_follows_the_size(editor):
    editor.items()
    drop(editor, "Room", 1500, 1500)
    editor.scene.clearSelection()
    editor.draw_border()
    editor.code_edit.setText("CU-031")
    editor.save_component()
    editor.new_component()
    editor.open_component("CU-031")
    border = only(editor, BorderItem)[0]
    editor.width_spin.setValue(14)
    assert border.area() == editor.scene.part_rect()


def test_parts_saved_before_borders_existed_still_open(editor, folders):
    solid(folders[2] / "CU-OLD.png", 4200, 4200, "white")
    custom.save_entry(
        "CU-OLD",
        {
            "url": "CU-OLD.png", "sizeX": 10, "sizeY": 10, "tons": 50, "uses": {},
            "facilities": {},
            "layout": [{"subpart": "Room", "x": 600, "y": 600, "turns": 0, "mirror": False}],
        },
        folders[2],
    )
    assert editor.open_component("CU-OLD")
    assert [item.name for item in editor.items()] == ["Room"]


def test_borders_and_doors_copy_and_paste(editor):
    border = editor.draw_border()
    door = editor._add_door(1650, 1200, False)
    editor.scene.clearSelection()
    border.setSelected(True)
    door.setSelected(True)
    editor.copy_selected()
    editor.paste()

    new_border = next(i for i in only(editor, BorderItem) if i is not border)
    new_door = next(i for i in only(editor, DoorItem) if i is not door)
    assert new_border.area() == border.area().translated(SQUARE, SQUARE)
    assert new_door.pos() == door.pos() + QPointF(SQUARE, SQUARE)
    assert not new_border.follows_part
    assert set(editor.selected()) == {new_border, new_door}


def test_a_component_can_be_only_borders_and_doors(editor):
    editor.draw_border()
    top = editor.scene.part_rect().top()
    editor._add_door(1050, top, False)
    editor.code_edit.setText("CU-032")
    assert editor.save_component() == "CU-032"


def test_new_component_clears_borders_and_doors(editor):
    editor.draw_border()
    editor._add_door(1650, 1200, False)
    editor.dirty = False
    editor.new_component()
    assert editor.items() == []


@pytest.mark.parametrize(
    "text, expected",
    [("Room 1", "Room 1"), ("../../x", "x"), ("a/b:c", "a-b-c"), ("  ", ""),
     ("Bridge.png", "Bridge-png"), ("Iván's Room", "Iván-s Room")],
)
def test_safe_names(text, expected):
    assert custom.safe_name(text) == expected


def test_next_code_fills_gaps():
    assert custom.next_code({"CU-001", "CU-003"}) == "CU-002"


# The main app


@pytest.fixture
def window(app, folders, library):
    source, subparts, custom_folder = folders
    store = ImageStore([custom_folder / "lowres"])
    main = MainWindow(library, store, HighResStore(store, source, custom_folder))
    main.custom_folder = custom_folder
    main.show()
    QApplication.processEvents()
    yield main
    main.dirty = False
    for tool in main.tool_windows:
        tool.dirty = False
    main.close()


def wait_for(condition, timeout=3000):
    waited = 0
    while not condition() and waited < timeout:
        QTest.qWait(20)
        waited += 20
    return condition()


def test_the_preview_is_double_height(window):
    assert window.preview.height() == 440


def choose_part(window, code):
    window.search.setText(code)
    window.library_view.setCurrentIndex(window.library_model.index(0))


def test_the_preview_shows_the_high_res_image(app, tmp_path, library):
    url = library["SS-001"].url
    solid(tmp_path / "low" / url, 84, 84, "red")
    solid(tmp_path / "high" / url, 420, 420, "blue")
    store = ImageStore([tmp_path / "low"])
    main = MainWindow(library, store, HighResStore(store, tmp_path / "high"))
    main.show()
    choose_part(main, "SS-001")
    middle = lambda: main.preview.pixmap().toImage().pixelColor(50, 50)
    assert middle() == QColor("red")  # low-res shows immediately
    assert wait_for(lambda: middle() == QColor("blue"))
    main.dirty = False
    main.close()


def test_a_stale_preview_is_ignored(app, tmp_path, library):
    url = library["SS-001"].url
    solid(tmp_path / "low" / url, 84, 84, "red")
    store = ImageStore([tmp_path / "low"])
    main = MainWindow(library, store, HighResStore(store, NO_FOLDER, NO_FOLDER))
    main.show()
    choose_part(main, "SS-001")
    stale = QImage(10, 10, QImage.Format_RGB32)
    stale.fill(QColor("blue"))
    main._high_res_preview_ready(main._preview_request - 1, stale)
    assert main.preview.pixmap().toImage().pixelColor(50, 50) == QColor("red")
    main.dirty = False
    main.close()


def test_the_high_res_preview_keeps_a_tiles_real_shape(app, tmp_path, library):
    url = library["SS-001"].url
    solid(tmp_path / "high" / url, 400, 800, "blue")
    store = ImageStore([])
    main = MainWindow(library, store, HighResStore(store, tmp_path / "high", NO_FOLDER))
    main.show()
    choose_part(main, "SS-001")
    assert wait_for(lambda: main.preview.pixmap() is not None
                    and not main.preview.pixmap().isNull())
    picture = main.preview.pixmap()
    assert abs(picture.height() / picture.width() - 2) < 0.02
    main.dirty = False
    main.close()


def test_custom_parts_have_their_own_filter(window):
    keys = [
        window.shape_list.item(row).data(Qt.UserRole)
        for row in range(window.shape_list.count())
    ]
    assert keys[0] == "Custom"


def test_the_tools_open_from_the_menu_only_once(window):
    cutter = window.open_cutter()
    assert isinstance(cutter, CutterWindow)
    assert window.open_cutter() is cutter
    editor = window.open_editor()
    assert isinstance(editor, ComponentEditor)
    assert window.open_editor() is editor
    assert editor.custom_folder == window.custom_folder


def test_a_saved_part_appears_in_the_open_library(window, folders):
    _, subparts, _ = folders
    quarters(subparts / "Room.png", 600)
    editor = window.open_editor()
    editor.subpart_folder = subparts
    editor.refresh_subparts()
    save_part(editor, "CU-010")

    assert window.library["CU-010"].shape == "Custom"
    window.search.setText("CU-010")
    assert [part.code for part in window.library_model.parts] == ["CU-010"]


def test_a_custom_part_works_on_the_map(window, folders):
    _, subparts, _ = folders
    quarters(subparts / "Room.png", 600)
    editor = window.open_editor()
    editor.subpart_folder = subparts
    save_part(editor, "CU-011")

    window.add_part("CU-011")
    item = window.items[0]
    assert window.images.pixmap(item.info.url) is not None
    assert window.ship.parts[0].code == "CU-011"
    assert window.render_image() is not None


def test_a_custom_part_uses_the_low_res_image_on_the_map_only(window, folders):
    _, subparts, custom_folder = folders
    quarters(subparts / "Room.png", 600)
    editor = window.open_editor()
    editor.subpart_folder = subparts
    save_part(editor, "CU-012")
    url = window.library["CU-012"].url
    assert window.images.pixmap(url).width() == 840
    assert window.high_res.pixmap(url).width() == 14 * SQUARE


def test_resaving_a_part_updates_the_map(window, folders, monkeypatch):
    _, subparts, _ = folders
    quarters(subparts / "Room.png", 600)
    editor = window.open_editor()
    editor.subpart_folder = subparts
    save_part(editor, "CU-013")
    window.add_part("CU-013")
    url = window.library["CU-013"].url
    first = window.images.pixmap(url).toImage()

    editor.items()[0].set_pose(2, False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    assert editor.save_component() == "CU-013"
    assert window.images.pixmap(url).toImage() != first


def test_a_ship_with_a_custom_part_round_trips(window, folders, tmp_path):
    _, subparts, _ = folders
    quarters(subparts / "Room.png", 600)
    editor = window.open_editor()
    editor.subpart_folder = subparts
    save_part(editor, "CU-014")
    window.add_part("CU-014")
    path = tmp_path / "ship.json"
    assert window.save_to(path)
    assert window.open_path(path)
    assert window.ship.parts[0].code == "CU-014"


def test_deleted_files_leave_the_library_on_reload(window, folders):
    part_on_disk(folders[2], "CU-080")
    part_on_disk(folders[2], "CU-081")
    window.reload_custom_parts()
    assert "CU-080" in window.library and "CU-081" in window.library

    (folders[2] / "CU-080.png").unlink()
    window.reload_custom_parts()
    assert "CU-080" not in window.library
    assert "CU-081" in window.library


def test_a_part_on_the_open_ship_survives_its_files_going(window, folders):
    part_on_disk(folders[2], "CU-082")
    window.reload_custom_parts()
    window.add_part("CU-082")

    (folders[2] / "CU-082.png").unlink()
    window.reload_custom_parts()

    assert "CU-082" in window.library
    window.rotate_cw()
    assert window.tonnage_table.rowCount() >= 1


def test_switching_back_to_the_app_updates_the_library(window, folders):
    part_on_disk(folders[2], "CU-083")
    window.reload_custom_parts()
    window._refresh_library()
    (folders[2] / "CU-083.png").unlink()

    window.isActiveWindow = lambda: True
    QApplication.sendEvent(window, QEvent(QEvent.ActivationChange))

    assert "CU-083" not in window.library
    assert "CU-083" not in [part.code for part in window.library_model.parts]
