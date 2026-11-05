"""The Subpart Cutter, the Component Editor and how they join up."""

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QDropEvent, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from shipyard import custom, model
from shipyard.app import DATA_DIR, MainWindow
from shipyard.cutter import CutterWindow, snapped
from shipyard.editor import SUBPART_MIME, ComponentEditor, canvas_size, compose
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
    cutter.name_edit.setText("Red corner")
    assert cutter.save_subpart() == "Red corner"

    image = QImage(str(subparts / "Red corner.png"))
    assert (image.width(), image.height()) == (600, 600)
    assert image.pixelColor(10, 10) == QColor("red")
    record = json.loads((subparts / "Red corner.json").read_text())
    assert record["source"].endswith("SS-001 [Port] [50x50] Fuel.png")
    assert (record["x"], record["y"], record["width"]) == (0, 0, 600)
    assert "CC BY-NC" in record["licence"]


def test_a_name_is_suggested_from_the_tile(cutter):
    drag_box(cutter, (300, 600), (900, 900))
    assert cutter.name_edit.text() == "SS-001 1_2 2x1"


def test_a_typed_name_is_not_replaced(cutter):
    cutter.name_edit.setText("My room")
    cutter.name_edit.setModified(True)
    drag_box(cutter, (300, 300), (600, 600))
    assert cutter.name_edit.text() == "My room"


def test_the_box_clears_after_saving_ready_for_the_next(cutter):
    drag_box(cutter, (0, 0), (300, 300))
    cutter.save_subpart()
    assert cutter.view.selection().isEmpty()
    drag_box(cutter, (600, 0), (900, 300))
    assert cutter.name_edit.text() == "SS-001 2_0 1x1"


def test_replacing_a_subpart_asks_first(cutter, monkeypatch, folders):
    drag_box(cutter, (0, 0), (300, 300))
    cutter.name_edit.setText("Room")
    cutter.save_subpart()
    drag_box(cutter, (600, 600), (900, 900))
    cutter.name_edit.setText("Room")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Cancel)
    assert cutter.save_subpart() is None
    image = QImage(str(folders[1] / "Room.png"))
    assert image.pixelColor(10, 10) == QColor("red")


def test_a_hostile_name_stays_in_the_folder(cutter, folders):
    drag_box(cutter, (0, 0), (300, 300))
    cutter.name_edit.setText("../../evil")
    assert cutter.save_subpart() == "evil"
    assert (folders[1] / "evil.png").exists()


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


def test_a_broken_custom_file_does_not_stop_the_editor(editor, folders):
    (folders[2]).mkdir(exist_ok=True)
    (folders[2] / custom.CUSTOM_FILE_NAME).write_text("not json")
    assert editor._custom_entries() == {}
    assert custom.CUSTOM_FILE_NAME in editor.statusBar().currentMessage()


def test_closing_with_unsaved_work_asks(editor, monkeypatch):
    drop(editor, "Room", 900, 900)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Cancel)
    assert not editor.close()
    assert editor.isVisible()


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
