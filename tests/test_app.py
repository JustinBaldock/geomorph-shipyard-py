"""Drives the real window with no screen, using Qt's offscreen platform."""

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QToolBar

from shipyard.app import DATA_DIR, MainWindow
from shipyard.images import HighResStore, ImageStore
from shipyard.model import Part, Ship, load_library, place


NO_FOLDER = Path("/nonexistent/geomorphs")


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def library():
    return load_library(DATA_DIR)


@pytest.fixture
def window(app, library):
    window = MainWindow(library, ImageStore([]), HighResStore(ImageStore([]), NO_FOLDER))
    window.show()
    QApplication.processEvents()
    window.dirty = False
    yield window
    window.dirty = False
    window.close()


def view_point(window, x, y):
    """A viewport position for a point in map units."""
    return window.view.mapFromScene(QPointF(x, -y))


def key(window, key_code, modifier=Qt.NoModifier):
    QTest.keyClick(window.view, key_code, modifier)
    QApplication.processEvents()


def test_starts_with_the_whole_library_and_no_ship(window, library):
    assert window.library_model.rowCount() == len(library)
    assert window.deck.parts == []
    assert "No geomorph images" in window.statusBar().currentMessage()


def test_add_places_a_part_at_the_map_centre_and_selects_it(window):
    window.add_part("SS-001")
    part = window.deck.parts[0]
    assert part.code == "SS-001"
    assert part.rotation == 0
    assert part.corner[0] % 5 == 0 and part.corner[1] % 5 == 0
    assert window.selected_index() == 0
    assert window.dirty


def test_rotate_keys(window):
    window.add_part("SS-001")
    window.view.setFocus()
    window.rotate_cw()
    assert window.deck.parts[0].rotation == 270
    window.rotate_ccw()
    assert window.deck.parts[0].rotation == 0


def test_arrow_keys_move_by_one_square(window):
    window.add_part("SS-001")
    x, y = window.deck.parts[0].corner
    key(window, Qt.Key_Left)
    assert window.deck.parts[0].corner == (x - 5, y)
    key(window, Qt.Key_Up)
    assert window.deck.parts[0].corner == (x - 5, y + 5)
    key(window, Qt.Key_Right)
    key(window, Qt.Key_Down)
    assert window.deck.parts[0].corner == (x, y)


def test_click_selects_and_click_on_empty_space_deselects(window):
    window.deck.parts.append(Part("SS-001", (0, 0)))
    window._rebuild_items()
    viewport = window.view.viewport()

    QTest.mouseClick(viewport, Qt.LeftButton, pos=view_point(window, 25, 25))
    assert window.selected_index() == 0

    QTest.mouseClick(viewport, Qt.LeftButton, pos=view_point(window, -40, -40))
    assert window.selected_index() is None


def test_clicking_the_image_border_does_not_select(window):
    window.deck.parts.append(Part("SS-001", (0, 0)))
    window._rebuild_items()
    point = view_point(window, -5, 25)  # inside the two-square image border
    QTest.mouseClick(window.view.viewport(), Qt.LeftButton, pos=point)
    assert window.selected_index() is None


def test_drag_moves_a_part_and_snaps_to_squares(window):
    window.deck.parts.append(Part("SS-001", (0, 0)))
    window._rebuild_items()
    viewport = window.view.viewport()
    start = view_point(window, 25, 25)
    end = view_point(window, 38, 12)

    QTest.mousePress(viewport, Qt.LeftButton, pos=start)
    QTest.mouseMove(viewport, pos=(start + end) / 2)
    QTest.mouseMove(viewport, pos=end)
    QTest.mouseRelease(viewport, Qt.LeftButton, pos=end)
    QApplication.processEvents()

    x, y = window.deck.parts[0].corner
    assert x % 5 == 0 and y % 5 == 0
    assert (x, y) != (0, 0)
    assert abs(x - 13) <= 3 and abs(y + 13) <= 3
    assert window.dirty


def test_dragging_empty_space_pans_without_moving_parts(window):
    window.deck.parts.append(Part("SS-001", (0, 0)))
    window._rebuild_items()
    viewport = window.view.viewport()
    before = window.view.mapToScene(viewport.rect().center())

    QTest.mousePress(viewport, Qt.LeftButton, pos=QPoint(40, 40))
    QTest.mouseMove(viewport, pos=QPoint(90, 90))
    QTest.mouseMove(viewport, pos=QPoint(140, 140))
    QTest.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(140, 140))
    QApplication.processEvents()

    assert window.view.mapToScene(viewport.rect().center()) != before
    assert window.deck.parts[0].corner == (0, 0)


def test_wheel_zooms_within_limits(window):
    view = window.view
    position = QPointF(200, 200)
    global_position = view.viewport().mapToGlobal(position)

    def wheel(delta):
        event = QWheelEvent(
            position,
            global_position,
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(view.viewport(), event)

    start = view.transform().m11()
    wheel(240)
    assert view.transform().m11() == pytest.approx(start * 2)
    for _ in range(20):
        wheel(240)
    assert view.transform().m11() == pytest.approx(8)
    for _ in range(40):
        wheel(-240)
    assert view.transform().m11() == pytest.approx(1 / 8)


def test_clone_and_paste_add_the_same_part(window):
    window.add_part("SS-001")
    window.clone_selected()
    assert [part.code for part in window.deck.parts] == ["SS-001", "SS-001"]
    window.copy_selected()
    window.paste()
    assert len(window.deck.parts) == 3


def test_cut_removes_and_paste_brings_back(window):
    window.add_part("SS-001")
    window.cut_selected()
    assert window.deck.parts == []
    window.paste()
    assert window.deck.parts[0].code == "SS-001"


def test_delete_clears_the_selection(window):
    window.add_part("SS-001")
    window.delete_selected()
    assert window.deck.parts == []
    assert window.selected_item() is None
    assert not window.outline.isVisible()


def test_replace_keeps_the_position_and_clears_the_cargo(window, library):
    overlay_code = next(c for c, i in library.items() if i.overlay_url)
    window.add_part(overlay_code)
    window.toggle_overlay()
    corner = window.deck.parts[0].corner
    assert window.deck.parts[0].overlay

    row = next(
        i for i, info in enumerate(window.library_model.parts) if info.code == "SS-001"
    )
    window.library_view.setCurrentIndex(window.library_model.index(row))
    window.replace_with_current()

    assert window.deck.parts[0] == Part("SS-001", corner, 0, False, False)


def test_flip_only_works_on_parts_with_a_mirror(window, library):
    plain = next(c for c, i in library.items() if not i.mirror_url)
    window.add_part(plain)
    assert not window.flip_action.isEnabled()
    window.toggle_mirror()
    assert not window.deck.parts[0].mirror

    window.add_part("SS-001")
    assert window.flip_action.isEnabled()
    window.toggle_mirror()
    assert window.deck.parts[1].mirror


def test_cargo_only_works_on_parts_with_an_overlay(window, library):
    plain = next(c for c, i in library.items() if not i.overlay_url)
    window.add_part(plain)
    assert not window.overlay_action.isEnabled()
    window.toggle_overlay()
    assert not window.deck.parts[0].overlay


def test_library_filters_update_the_list(window):
    window.search.setText("SS-00")
    assert 0 < window.library_model.rowCount() < 20
    assert "of" in window.count_label.text()
    window.search.clear()

    window.usage_list.item(0).setCheckState(Qt.Checked)
    filtered = window.library_model.rowCount()
    window.usage_list.item(0).setCheckState(Qt.Unchecked)
    assert filtered < window.library_model.rowCount()


def test_tonnage_table_follows_the_ship(window):
    window.add_part("SS-001")
    table = window.tonnage_table
    assert [table.item(0, c).text() for c in range(3)] == ["Fuel", "8", "100%"]
    assert [table.item(1, c).text() for c in range(2)] == ["Total", "8"]
    window.delete_selected()
    assert table.item(0, 1).text() == "0"


def test_parts_used_table_selects_parts(window):
    window.add_part("SS-001")
    window.add_part("SS-002")
    window.used_table.selectRow(0)
    assert window.selected_index() == 0
    assert window.used_table.item(1, 0).text() == "SS-002"


def test_changing_the_name_updates_the_title(window):
    window.name_edit.setText("Courier")
    window._name_edited("Courier")
    assert window.windowTitle().startswith("Courier - Geomorph Shipyard")
    assert window.ship.name == "Courier"


def test_save_and_open_round_trip(window, tmp_path):
    window.add_part("SS-001")
    window.rotate_cw()
    path = tmp_path / "ship.json"
    assert window.save_to(path)
    assert not window.dirty
    saved = list(window.deck.parts)

    window.ship = Ship.empty()
    window._rebuild_items()
    assert window.open_path(path)
    assert window.deck.parts == saved
    assert len(window.items) == 1
    assert json.loads(path.read_text())["meta"] == "Traveller Geomorph Ship"


def test_a_bad_file_leaves_the_ship_alone(window, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args: shown.append(args[2])
    )
    window.add_part("SS-001")
    path = tmp_path / "bad.json"
    path.write_text("not json")
    assert not window.open_path(path)
    assert len(window.deck.parts) == 1
    assert "not valid JSON" in shown[0]


def test_closing_with_changes_asks_first(window, monkeypatch):
    asked = []

    def answer(*args, **kwargs):
        asked.append(True)
        return QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, "question", answer)
    window.add_part("SS-001")
    assert not window.close()
    assert asked and window.isVisible()


def test_export_includes_the_credit_and_hides_the_outline(window, tmp_path):
    assert window.render_image() is None
    window.add_part("SS-001")
    assert window.outline.isVisible()
    image = window.render_image()
    assert image.width() > 100 and image.height() > 100
    assert window.outline.isVisible()

    path = tmp_path / "ship.png"
    assert image.save(str(path), "PNG")
    assert QImage(str(path)).size() == image.size()


def colour_at(image, x, y):
    return image.pixelColor(int(x), int(y)).getRgb()[:3]


def make_test_image(path):
    """A 70 by 70 unit image, red at its top left and blue at its bottom right."""
    image = QImage(560, 560, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.fillRect(0, 0, 280, 280, QColor("red"))
    painter.fillRect(280, 280, 280, 280, QColor("blue"))
    painter.end()
    assert image.save(str(path))


@pytest.mark.parametrize(
    "rotation, red, blue",
    [
        (0, (10, 40), (40, 10)),  # red north-west, blue south-east
        (90, (10, 10), (40, 40)),  # a quarter turn left: red south-west
        (180, (40, 10), (10, 40)),
        (270, (40, 40), (10, 10)),  # a quarter turn right: red north-east
    ],
)
def test_images_land_the_right_way_round(app, library, tmp_path, rotation, red, blue):
    folder = tmp_path / "images"
    image_path = folder / library["SS-001"].url
    image_path.parent.mkdir(parents=True)
    make_test_image(image_path)

    window = MainWindow(library, ImageStore([folder]))
    window.deck.parts.append(Part("SS-001", (0, 0), rotation))
    window._rebuild_items()
    window.view.resetTransform()

    area = window.items[0].sceneBoundingRect()
    assert area.width() == pytest.approx(72)  # 70 plus the one unit of padding
    image = QImage(70, 70, QImage.Format_RGB32)
    painter = QPainter(image)
    from PySide6.QtCore import QRectF

    window.scene.render(painter, QRectF(0, 0, 70, 70), QRectF(-10, -60, 70, 70))
    painter.end()

    def at(point):  # map (x, y) to a pixel in this 1:1 render
        return colour_at(image, point[0] + 10, 60 - point[1])

    assert at(red) == (255, 0, 0)
    assert at(blue) == (0, 0, 255)
    window.dirty = False
    window.close()


def test_placement_sizes_match_the_data(library):
    info = library["SS-001"]
    placement = place(Part("SS-001", (0, 0)), info)
    assert placement.bounds == (0, 0, 50, 50)


def two_decks(window):
    window.add_part("SS-001")
    window.add_deck()
    window.add_part("SS-002")
    window.add_part("SS-003")


def test_a_new_ship_has_one_deck(window):
    assert window.deck_tabs.count() == 1
    assert window.deck_tabs.tabText(0) == "Deck 1"
    assert not window.delete_deck_action.isEnabled()


def test_add_deck_opens_an_empty_deck(window):
    window.add_part("SS-001")
    window.add_deck()
    assert window.current == 1
    assert window.deck_tabs.count() == 2
    assert window.deck.parts == []
    assert window.items == []
    assert window.dirty


def test_parts_stay_on_their_own_deck(window):
    two_decks(window)
    assert [len(deck.parts) for deck in window.ship.decks] == [1, 2]
    assert len(window.items) == 2
    window.deck_tabs.setCurrentIndex(0)
    assert window.current == 0
    assert [item.part.code for item in window.items] == ["SS-001"]


def test_switching_deck_clears_the_selection(window):
    two_decks(window)
    assert window.selected_item() is not None
    window.switch_deck(0)
    assert window.selected_item() is None
    assert not window.outline.isVisible()


def test_page_keys_step_through_decks(window):
    two_decks(window)
    window.step_deck(1)
    assert window.current == 0
    window.step_deck(-1)
    assert window.current == 1


def test_duplicate_deck_copies_parts_and_opens_the_copy(window):
    window.add_part("SS-001")
    window.duplicate_deck()
    assert window.current == 1
    assert window.deck.name == "Deck 1 copy"
    assert len(window.items) == 1
    window.select_index(0)
    window.rotate_cw()
    assert window.ship.decks[0].parts[0].rotation == 0


def test_rename_updates_the_tabs_and_trace_list(window):
    two_decks(window)
    window.rename_deck(0, "  Upper  ")
    assert window.deck_tabs.tabText(0) == "Upper"
    assert window.trace_combo.findText("Upper") >= 0


def test_a_blank_name_is_ignored(window):
    window.rename_deck(0, "   ")
    assert window.deck.name == "Deck 1"


def test_rename_prompt_uses_the_answer(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(
        QInputDialog, "getText", lambda *a, **k: ("Bridge deck", True)
    )
    window.rename_deck_prompt(0)
    assert window.deck.name == "Bridge deck"


def test_deleting_a_deck_with_parts_asks_first(window, monkeypatch):
    two_decks(window)
    answers = [QMessageBox.Cancel, QMessageBox.Yes]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: answers.pop(0))
    window.delete_deck()
    assert len(window.ship.decks) == 2
    window.delete_deck()
    assert len(window.ship.decks) == 1
    assert window.current == 0
    assert window.deck_tabs.count() == 1


def test_deleting_an_empty_deck_does_not_ask(window, monkeypatch):
    window.add_deck()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: pytest.fail("asked"))
    window.delete_deck()
    assert len(window.ship.decks) == 1


def test_the_last_deck_stays(window):
    window.delete_deck()
    assert len(window.ship.decks) == 1


def test_dragging_a_tab_reorders_decks(window):
    two_decks(window)
    window.deck_tabs.moveTab(1, 0)
    assert [deck.name for deck in window.ship.decks] == ["Deck 2", "Deck 1"]
    assert window.deck.name == "Deck 2"
    assert len(window.items) == 2


def test_move_a_part_to_another_deck(window):
    two_decks(window)
    window.select_index(0)
    assert window.move_menu.isEnabled()
    window.move_selected_to_deck(0)
    assert [p.code for p in window.ship.decks[0].parts] == ["SS-001", "SS-002"]
    assert [p.code for p in window.ship.decks[1].parts] == ["SS-003"]


def test_move_is_off_with_only_one_deck(window):
    window.add_part("SS-001")
    assert not window.move_menu.isEnabled()


def test_trace_shows_the_other_deck_without_touching_it(window):
    two_decks(window)
    index = window.trace_combo.findText("Deck 1")
    window.trace_combo.setCurrentIndex(index)
    window._trace_chosen(index)

    assert [item.part.code for item in window.trace_items] == ["SS-001"]
    trace = window.trace_items[0]
    assert trace.opacity() < 1
    assert not trace.flags() & trace.GraphicsItemFlag.ItemIsSelectable
    assert all(item.zValue() > trace.zValue() for item in window.items)


def test_a_traced_part_cannot_be_clicked(window):
    two_decks(window)
    window.ship.decks[0].parts[0].corner = (0, 0)
    window.deck.parts.clear()
    window._rebuild_items()
    window._trace_chosen(window.trace_combo.findText("Deck 1"))
    QTest.mouseClick(
        window.view.viewport(), Qt.LeftButton, pos=view_point(window, 25, 25)
    )
    assert window.selected_item() is None


def test_trace_is_dropped_when_its_deck_is_deleted(window, monkeypatch):
    two_decks(window)
    window._trace_chosen(window.trace_combo.findText("Deck 1"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    window.switch_deck(0)
    window.delete_deck()
    assert window.trace_deck is None
    assert window.trace_items == []


def test_tonnage_covers_the_ship_with_a_line_per_deck(window):
    two_decks(window)
    rows = window.tonnage_table
    assert rows.item(rows.rowCount() - 1, 0).text() == "Total"
    assert rows.item(rows.rowCount() - 1, 1).text() == "24"
    assert window.deck_table.item(0, 1).text() == "8"
    assert window.deck_table.item(1, 1).text() == "16"


def test_parts_used_lists_the_current_deck(window):
    two_decks(window)
    assert window.used_table.rowCount() == 2


def test_decks_survive_save_and_open_in_the_window(window, tmp_path):
    two_decks(window)
    window.rename_deck(0, "Upper")
    path = tmp_path / "ship.json"
    assert window.save_to(path)
    window._set_ship(Ship.empty(), None)
    assert window.open_path(path)
    assert [deck.name for deck in window.ship.decks] == ["Upper", "Deck 2"]
    assert window.deck_tabs.count() == 2
    assert window.current == 0


def test_export_one_deck_or_all(window):
    two_decks(window)
    one = window.render_image()
    both = window.render_image(window.ship.decks)
    assert both.height() > one.height() * 1.5
    assert both.width() >= one.width()


def test_export_lines_decks_up_and_leaves_tracing_out(window):
    window.add_part("SS-001")
    window.add_deck()
    window.add_part("SS-002")
    window._trace_chosen(window.trace_combo.findText("Deck 1"))
    plain = window.render_image(window.ship.decks)

    window.trace_deck = None
    window._rebuild_items()
    assert window.render_image(window.ship.decks) == plain


def test_export_skips_empty_decks(window):
    window.add_part("SS-001")
    window.add_deck()
    assert window.render_image() is None
    first_deck_only = window.render_image(window.ship.decks[:1])
    assert window.render_image(window.ship.decks).size() == first_deck_only.size()


def solid_image(path, width, height, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(colour))
    assert image.save(str(path))


def export_window(app, library, tmp_path, high_size):
    """A window with a red low-res tile and a blue high-res tile."""
    url = library["SS-001"].url
    solid_image(tmp_path / "low" / url, 84, 84, "red")
    solid_image(tmp_path / "high" / url, high_size[0], high_size[1], "blue")
    store = ImageStore([tmp_path / "low"])
    window = MainWindow(library, store, HighResStore(store, tmp_path / "high"))
    window.deck.parts.append(Part("SS-001", (0, 0)))
    window._rebuild_items()
    return window


def centre_colour(image):
    return colour_at(image, image.width() / 2, (image.height() - 28) / 2)


def test_high_res_export_uses_the_high_res_tile(app, library, tmp_path):
    window = export_window(app, library, tmp_path, (420, 420))
    assert window.high_res_action.isChecked()
    assert centre_colour(window.render_image()) == (0, 0, 255)

    window.high_res_action.setChecked(False)
    assert centre_colour(window.render_image()) == (255, 0, 0)
    window.dirty = False
    window.close()


def test_the_map_itself_stays_on_low_res(app, library, tmp_path):
    window = export_window(app, library, tmp_path, (420, 420))
    assert window.items[0].images is window.images
    window.dirty = False
    window.close()


def test_a_high_res_tile_of_another_shape_falls_back(app, library, tmp_path):
    window = export_window(app, library, tmp_path, (420, 510))
    assert centre_colour(window.render_image()) == (255, 0, 0)
    window.dirty = False
    window.close()


def test_high_res_export_is_larger(app, library, tmp_path):
    window = export_window(app, library, tmp_path, (420, 420))
    large = window.render_image()
    window.high_res_action.setChecked(False)
    assert large.width() > window.render_image().width() * 2
    window.dirty = False
    window.close()


def test_high_res_option_is_off_without_the_images(window):
    assert not window.high_res_action.isEnabled()
    assert not window.high_res_action.isChecked()


def test_images_over_qts_default_limit_load(app, tmp_path):
    path = tmp_path / "big.png"
    image = QImage(9000, 9000, QImage.Format_Indexed8)
    image.setColorCount(1)
    image.setColor(0, QColor("white").rgb())
    image.fill(0)
    assert image.save(str(path))
    assert ImageStore([tmp_path]).pixmap("big.png") is not None


def test_a_missing_image_is_a_placeholder_not_a_crash(window):
    assert window.images.pixmap("nothing/here.png") is None
    assert window.high_res.pixmap("nothing/here.png") is None


def row_of_three(window):
    """Three parts side by side, nothing selected."""
    window.deck.parts.extend(
        [Part("SS-001", (0, 0)), Part("SS-002", (50, 0)), Part("SS-003", (100, 0))]
    )
    window._rebuild_items()
    window.view.resetTransform()
    window.view.scale(2, 2)
    window.view.centerOn(75, -25)
    QApplication.processEvents()


def click(window, x, y, modifier=Qt.NoModifier):
    QTest.mouseClick(
        window.view.viewport(), Qt.LeftButton, modifier, view_point(window, x, y)
    )
    QApplication.processEvents()


def drag(window, start, end, modifier=Qt.NoModifier):
    viewport = window.view.viewport()
    first = view_point(window, *start)
    last = view_point(window, *end)
    QTest.mousePress(viewport, Qt.LeftButton, modifier, first)
    QTest.mouseMove(viewport, (first + last) / 2)
    QTest.mouseMove(viewport, last)
    QTest.mouseRelease(viewport, Qt.LeftButton, modifier, last)
    QApplication.processEvents()


def test_shift_click_adds_to_the_selection(window):
    row_of_three(window)
    click(window, 25, 25)
    click(window, 75, 25, Qt.ShiftModifier)
    assert window.selected_indices() == [0, 1]
    click(window, 125, 25, Qt.ShiftModifier)
    assert window.selected_indices() == [0, 1, 2]


def test_shift_click_with_nothing_selected_starts_a_selection(window):
    row_of_three(window)
    click(window, 75, 25, Qt.ShiftModifier)
    assert window.selected_indices() == [1]


def test_shift_click_on_a_selected_part_removes_it(window):
    row_of_three(window)
    window.select_indices([0, 1])
    click(window, 25, 25, Qt.ShiftModifier)
    assert window.selected_indices() == [1]


def test_a_plain_click_goes_back_to_one_part(window):
    row_of_three(window)
    window.select_indices([0, 1])
    click(window, 125, 25)
    assert window.selected_indices() == [2]


def test_a_plain_click_on_a_group_member_selects_only_it(window):
    row_of_three(window)
    window.select_indices([0, 1])
    click(window, 25, 25)
    assert window.selected_indices() == [0]


def test_clicking_empty_space_clears_a_group(window):
    row_of_three(window)
    window.select_indices([0, 1])
    click(window, 75, 120)
    assert window.selected_indices() == []


def test_a_shift_click_that_misses_keeps_the_group(window):
    row_of_three(window)
    window.select_indices([0, 1])
    click(window, 75, 120, Qt.ShiftModifier)
    assert window.selected_indices() == [0, 1]


def test_escape_action_clears_a_group(window):
    row_of_three(window)
    window.select_indices([0, 1])
    window.deselect()
    assert window.selected_indices() == []
    assert not window.outline.isVisible()


def test_select_all_takes_this_deck_only(window):
    row_of_three(window)
    window.add_deck()
    assert window.items == []
    window.select_all()
    assert window.selected_indices() == []
    window.switch_deck(0)
    window.select_all()
    assert window.selected_indices() == [0, 1, 2]


def test_every_selected_part_gets_an_outline(window):
    row_of_three(window)
    window.select_indices([0, 2])
    rect = window.outline.path().boundingRect()
    assert rect.width() == 150
    assert window.outline.path().elementCount() == 10


def test_dragging_a_group_moves_it_as_one(window):
    row_of_three(window)
    window.select_indices([0, 1])
    drag(window, (25, 25), (55, 45))
    corners = [part.corner for part in window.deck.parts]
    dx = corners[0][0]
    dy = corners[0][1]
    assert (dx, dy) != (0, 0) and dx % 5 == 0 and dy % 5 == 0
    assert corners[1] == (50 + dx, dy)
    assert corners[2] == (100, 0)
    assert window.selected_indices() == [0, 1]


def test_shift_dragging_moves_the_group_too(window):
    row_of_three(window)
    window.select_indices([0, 1])
    drag(window, (25, 25), (55, 45), Qt.ShiftModifier)
    corners = [part.corner for part in window.deck.parts]
    assert corners[0] != (0, 0)
    assert corners[1][0] - corners[0][0] == 50
    assert corners[2] == (100, 0)
    assert window.selected_indices() == [0, 1]


def test_dragging_a_part_outside_the_group_moves_only_it(window):
    row_of_three(window)
    window.select_indices([0, 1])
    drag(window, (125, 25), (135, 45))
    assert window.deck.parts[0].corner == (0, 0)
    assert window.deck.parts[2].corner != (100, 0)
    assert window.selected_indices() == [2]


def test_the_tables_catch_up_after_a_group_drag(window):
    row_of_three(window)
    window.select_indices([0, 1])
    drag(window, (25, 25), (55, 45))
    x, y = window.deck.parts[0].corner
    assert window.used_table.item(0, 1).text() == f"{x}, {y}"
    assert window.dirty


def test_the_outline_follows_a_group_drag(window):
    row_of_three(window)
    window.select_indices([0, 1])
    drag(window, (25, 25), (55, 45))
    rect = window.outline.path().boundingRect()
    assert rect.left() == window.deck.parts[0].corner[0]


def test_arrow_keys_move_a_group(window):
    row_of_three(window)
    window.select_indices([0, 2])
    key(window, Qt.Key_Right)
    assert [part.corner for part in window.deck.parts] == [
        (5, 0),
        (50, 0),
        (105, 0),
    ]
    assert window.selected_indices() == [0, 2]


def test_copy_and_paste_a_group_keeps_its_layout(window):
    row_of_three(window)
    window.deck.parts[0].rotation = 90
    window.deck.parts[0].mirror = True
    window._rebuild_items()
    window.select_indices([0, 2])
    window.copy_selected()
    window.paste()

    parts = window.deck.parts
    assert len(parts) == 5
    assert window.selected_indices() == [3, 4]
    assert parts[3].code == "SS-001" and parts[4].code == "SS-003"
    assert parts[3].rotation == 90 and parts[3].mirror
    assert parts[4].corner[0] - parts[3].corner[0] == 100
    assert parts[4].corner[1] == parts[3].corner[1]


def test_pasted_parts_are_independent_of_the_clipboard(window):
    row_of_three(window)
    window.select_all()
    window.copy_selected()
    window.paste()
    window.deck.parts[3].rotation = 180
    window.paste()
    assert window.deck.parts[6].rotation == 0


def test_a_pasted_group_can_be_dragged_straight_away(window):
    row_of_three(window)
    window.select_all()
    window.copy_selected()
    window.deck.parts.clear()
    window._rebuild_items()
    window.paste()
    before = [part.corner for part in window.deck.parts]
    key(window, Qt.Key_Up)
    assert [part.corner[1] for part in window.deck.parts] == [
        corner[1] + 5 for corner in before
    ]


def test_cut_a_group_and_paste_it_on_another_deck(window):
    row_of_three(window)
    window.select_indices([0, 1])
    window.cut_selected()
    assert [part.code for part in window.deck.parts] == ["SS-003"]
    assert window.selected_indices() == []

    window.add_deck()
    window.paste()
    assert [part.code for part in window.deck.parts] == ["SS-001", "SS-002"]
    assert len(window.ship.decks[0].parts) == 1


def test_delete_removes_the_whole_group(window):
    row_of_three(window)
    window.select_indices([0, 2])
    window.delete_selected()
    assert [part.code for part in window.deck.parts] == ["SS-002"]


def test_clone_copies_a_group_without_touching_the_clipboard(window):
    row_of_three(window)
    window.select_index(2)
    window.copy_selected()
    window.select_indices([0, 1])
    window.clone_selected()
    assert len(window.deck.parts) == 5
    assert window.selected_indices() == [3, 4]
    assert [part.code for part in window.clipboard] == ["SS-003"]


def test_move_a_group_to_another_deck(window):
    row_of_three(window)
    window.add_deck()
    window.switch_deck(0)
    window.select_indices([0, 2])
    assert window.move_menu.isEnabled()
    window.move_selected_to_deck(1)
    assert [part.code for part in window.ship.decks[0].parts] == ["SS-002"]
    assert [part.code for part in window.ship.decks[1].parts] == ["SS-001", "SS-003"]


def test_single_part_actions_turn_off_for_a_group(window):
    row_of_three(window)
    window.select_indices([0, 1])
    window.library_view.setCurrentIndex(window.library_model.index(0))
    assert not window.cw_action.isEnabled()
    assert not window.ccw_action.isEnabled()
    assert not window.flip_action.isEnabled()
    assert not window.overlay_action.isEnabled()
    assert not window.replace_button.isEnabled()
    for action in (window.cut_action, window.copy_action, window.delete_action):
        assert action.isEnabled()

    window.rotate_cw()
    window.toggle_mirror()
    assert all(part.rotation == 0 for part in window.deck.parts)
    assert not any(part.mirror for part in window.deck.parts)


def test_parts_used_table_shows_a_group(window):
    row_of_three(window)
    window.select_indices([0, 2])
    rows = window.used_table.selectionModel().selectedRows()
    assert sorted(row.row() for row in rows) == [0, 2]


def test_choosing_rows_in_parts_used_selects_a_group(window):
    row_of_three(window)
    model = window.used_table.selectionModel()
    table_model = window.used_table.model()
    from PySide6.QtCore import QItemSelectionModel

    window.used_table.selectRow(0)
    model.select(
        table_model.index(2, 0),
        QItemSelectionModel.Select | QItemSelectionModel.Rows,
    )
    assert window.selected_indices() == [0, 2]


def test_a_dragged_part_really_moves_on_screen(window):
    window.deck.parts.append(Part("SS-001", (0, 0)))
    window._rebuild_items()
    item = window.items[0]
    before = item.sceneBoundingRect()
    local = item.part_rect()

    drag(window, (25, 25), (55, 45))

    x, y = window.deck.parts[0].corner
    assert (x, y) != (0, 0)
    assert item.part_rect() == local
    assert item.sceneBoundingRect() == before.translated(x, -y)


def test_a_dragged_part_can_be_clicked_where_it_landed(window):
    window.deck.parts.append(Part("SS-001", (0, 0)))
    window._rebuild_items()
    drag(window, (25, 25), (65, 25))
    x, y = window.deck.parts[0].corner
    assert x >= 30

    window.deselect()
    click(window, x + 45, y + 25)  # only inside the part if it really moved
    assert window.selected_indices() == [0]
    click(window, x / 2, y + 25)  # the old spot is now empty
    assert window.selected_indices() == []


def test_a_dragged_image_is_drawn_in_its_new_place(app, library, tmp_path):
    folder = tmp_path / "images"
    image_path = folder / library["SS-001"].url
    image_path.parent.mkdir(parents=True)
    solid = QImage(84, 84, QImage.Format_RGB32)
    solid.fill(QColor("red"))
    assert solid.save(str(image_path))

    store = ImageStore([folder])
    window = MainWindow(library, store, HighResStore(store, NO_FOLDER))
    window.deck.parts.append(Part("SS-001", (0, 0)))
    window._rebuild_items()
    window.resize(1000, 800)
    window.show()
    window.view.resetTransform()
    window.view.scale(4, 4)
    window.view.centerOn(25, -25)
    QApplication.processEvents()

    drag(window, (25, 25), (50, 25))
    QApplication.processEvents()
    x, y = window.deck.parts[0].corner
    assert x > 0

    picture = window.view.viewport().grab().toImage()
    inside = view_point(window, x + 40, y + 25)
    assert picture.pixelColor(inside).red() == 255
    assert picture.pixelColor(inside).green() == 0
    window.dirty = False
    window.close()


def test_copy_and_paste_are_on_the_top_bar(window):
    bar = window.findChild(QToolBar)
    assert window.copy_action in bar.actions()
    assert window.paste_action in bar.actions()
    assert window.cut_action not in bar.actions()


def test_cut_stays_on_its_keyboard_shortcut(window):
    window.add_part("SS-001")
    assert window.cut_action.isEnabled()
    key(window, Qt.Key_X, Qt.ControlModifier)
    assert window.deck.parts == []
    assert window.paste_action.isEnabled()


def test_top_bar_buttons_follow_the_selection_and_clipboard(window):
    assert not window.cut_action.isEnabled()
    assert not window.copy_action.isEnabled()
    assert not window.paste_action.isEnabled()

    window.add_part("SS-001")
    assert window.cut_action.isEnabled() and window.copy_action.isEnabled()
    assert not window.paste_action.isEnabled()

    window.copy_action.trigger()
    assert window.paste_action.isEnabled()
    window.paste_action.trigger()
    assert len(window.deck.parts) == 2
    window.cut_action.trigger()
    assert len(window.deck.parts) == 1


def test_the_compass_starts_with_the_ship(window):
    labels = window.orientation.labels()
    assert labels["left"] == "Bow / Front"
    assert labels["right"] == "Stern / Back"
    assert labels["up"] == "Starboard / Right"
    assert labels["down"] == "Port / Left"
    assert window.orientation.isVisible()


def test_the_compass_buttons_turn_the_ship_bow(window):
    window.orientation.right_button.click()
    assert window.ship.bow == "up"
    assert window.orientation.labels()["up"] == "Bow / Front"
    assert window.dirty

    window.orientation.left_button.click()
    assert window.ship.bow == "left"


def test_turning_the_compass_leaves_the_parts_alone(window):
    window.add_part("SS-001")
    before = (window.deck.parts[0].corner, window.deck.parts[0].rotation)
    window.turn_bow(1)
    assert (window.deck.parts[0].corner, window.deck.parts[0].rotation) == before


def test_menu_actions_turn_the_bow(window):
    window.turn_bow_right_action.trigger()
    assert window.ship.bow == "up"
    window.turn_bow_left_action.trigger()
    assert window.ship.bow == "left"


def test_the_compass_can_be_hidden(window):
    window.show_orientation_action.setChecked(False)
    assert not window.orientation.isVisible()
    assert not window.turn_bow_right_action.isEnabled()
    window.show_orientation_action.setChecked(True)
    assert window.orientation.isVisible()


def test_the_compass_stays_put_when_the_map_pans_and_zooms(window):
    before = window.orientation.geometry()
    window.view.centerOn(500, 500)
    window.view.scale(2, 2)
    QApplication.processEvents()
    assert window.orientation.geometry() == before


def test_clicks_on_the_compass_do_not_reach_the_map(window):
    window.add_part("SS-001")
    QTest.mouseClick(window.orientation, Qt.LeftButton, pos=QPoint(98, 98))
    QApplication.processEvents()
    assert window.selected_indices() == [0]


def test_opening_a_ship_shows_its_saved_direction(window, tmp_path):
    window.turn_bow(1)
    window.turn_bow(1)
    path = tmp_path / "ship.json"
    assert window.save_to(path)
    assert json.loads(path.read_text())["bow"] == "right"

    window._set_ship(Ship.empty(), None)
    assert window.orientation.labels()["left"] == "Bow / Front"

    assert window.open_path(path)
    assert window.ship.bow == "right"
    assert window.orientation.labels()["right"] == "Bow / Front"
    assert not window.dirty


def test_a_new_ship_goes_back_to_the_default_direction(window):
    window.turn_bow(1)
    window.dirty = False
    window.new_ship()
    assert window.ship.bow == "left"
    assert window.orientation.bow == "left"


def test_the_compass_is_not_in_exports(window):
    window.add_part("SS-001")
    plain = window.render_image()
    window.turn_bow(1)
    assert window.render_image() == plain


def test_the_compass_minimises_and_comes_back(window):
    overlay = window.orientation
    full = overlay.size()
    assert not overlay.compact
    assert overlay.size_button.text() == "−"

    overlay.size_button.click()
    assert overlay.compact
    assert overlay.width() < full.width() and overlay.height() < full.height()
    assert overlay.size_button.text() == "□"

    overlay.size_button.click()
    assert not overlay.compact
    assert overlay.size() == full
    assert overlay.size_button.text() == "−"


def test_the_size_button_is_in_the_top_left_corner(window):
    overlay = window.orientation
    for compact in (False, True):
        overlay.set_compact(compact)
        assert overlay.size_button.pos() == QPoint(6, 6)


def test_the_compass_stays_in_its_corner_when_minimised(window):
    before = window.orientation.pos()
    window.orientation.set_compact(True)
    assert window.orientation.pos() == before


def test_rotate_buttons_stay_inside_the_minimised_compass(window):
    overlay = window.orientation
    overlay.set_compact(True)
    area = overlay.rect()
    for button in (overlay.left_button, overlay.right_button, overlay.size_button):
        assert button.isVisible()
        assert area.contains(button.geometry())
    assert not overlay.left_button.geometry().intersects(
        overlay.right_button.geometry()
    )


def test_buttons_miss_the_hull_in_every_direction(window):
    overlay = window.orientation
    overlay.set_compact(True)
    centre = overlay.rect().center()
    reach = 24
    hull = overlay.rect().adjusted(
        centre.x() - reach, centre.y() - reach,
        reach - centre.x(), reach - centre.y(),
    )
    side = 11
    upright = hull.adjusted(reach - side, 0, side - reach, 0)
    sideways = hull.adjusted(0, reach - side, 0, side - reach)
    for button in (overlay.left_button, overlay.right_button, overlay.size_button):
        assert not upright.intersects(button.geometry())
        assert not sideways.intersects(button.geometry())


def test_the_minimised_compass_still_turns_the_bow(window):
    overlay = window.orientation
    overlay.set_compact(True)
    overlay.right_button.click()
    assert window.ship.bow == "up"
    assert overlay.compact


def test_the_red_bow_still_shows_when_minimised(window):
    overlay = window.orientation
    overlay.set_compact(True)
    overlay.set_bow("up")
    picture = overlay.grab().toImage()
    middle = overlay.rect().center()
    tip = picture.pixelColor(middle.x(), middle.y() - 16)
    assert tip.red() > 150 and tip.green() < 80


def test_the_text_is_only_drawn_at_full_size(window):
    def has_dark_text(picture, middle):
        return any(
            picture.pixelColor(x, y).red() < 120
            for x in range(middle - 12, middle + 12)
            for y in range(9, 21)
        )

    overlay = window.orientation
    overlay.set_bow("down")  # puts the dark Stern label on the top edge
    assert has_dark_text(overlay.grab().toImage(), overlay.width() // 2)

    overlay.set_compact(True)
    assert not has_dark_text(overlay.grab().toImage(), overlay.width() // 2)


def test_minimising_is_not_a_change_to_the_ship(window):
    window.orientation.set_compact(True)
    assert not window.dirty
    assert "compact" not in json.dumps(window.ship.to_json())


def test_the_compass_stays_minimised_when_another_ship_opens(window):
    window.orientation.set_compact(True)
    window._set_ship(Ship.empty(), None)
    assert window.orientation.compact


def test_hiding_and_showing_keeps_the_minimised_size(window):
    window.orientation.set_compact(True)
    window.show_orientation_action.setChecked(False)
    window.show_orientation_action.setChecked(True)
    assert window.orientation.compact
