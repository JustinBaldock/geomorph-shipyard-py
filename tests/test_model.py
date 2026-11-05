"""Checks that the rules match the original web app."""

import json
from pathlib import Path

import pytest

from shipyard.model import (
    SHIP_META,
    Deck,
    Part,
    PartInfo,
    Ship,
    ShipFileError,
    copies_centred_on,
    sides_for_bow,
    turn_bow,
    filter_library,
    group_bounds,
    load_library,
    percent,
    place,
    snap,
    suggested_filename,
    tonnage,
    total_tons,
)

DATA = Path(__file__).parent.parent / "shipyard" / "data" / "geomorphs"


@pytest.fixture(scope="module")
def library():
    return load_library(DATA)


def info(size_x=10, size_y=20, **kwargs):
    defaults = dict(
        code="T-1", shape="LG", tons=8, uses={}, facilities={}, url="T/T-1.png"
    )
    return PartInfo(size_x=size_x, size_y=size_y, **{**defaults, **kwargs})


def test_library_loads_every_part(library):
    assert len(library) >= 2000
    assert library["SS-001"].shape == "SS"
    assert library["SS-001"].url == "SS/SS-001 [Starboard] [50x50] [8-dTons] Fuel.png"
    assert library["SS-001"].mirror_url.startswith("SS/SS-001 [Port]")


def test_no_image_path_keeps_a_relative_prefix(library):
    for part in library.values():
        for url in (part.url, part.mirror_url, part.overlay_url):
            assert not url or not url.startswith(("../", "geomorphs/"))


# Expected values worked through by hand from the formulas in the original's
# +page.svelte, for a 10 by 20 square part with its corner at (100, 200).
# x1 = 90, y1 = 190, width = 70, length = 120.
@pytest.mark.parametrize(
    "rotation, top_left, top_right, bottom_left, bounds",
    [
        (0, (90, 310), (160, 310), (90, 190), (100, 200, 150, 300)),
        (90, (90, 190), (90, 260), (210, 190), (100, 200, 200, 250)),
        (180, (160, 190), (90, 190), (160, 310), (100, 200, 150, 300)),
        (270, (210, 260), (210, 190), (90, 260), (100, 200, 200, 250)),
    ],
)
def test_placement_matches_the_original(
    rotation, top_left, top_right, bottom_left, bounds
):
    placement = place(Part("T-1", (100, 200), rotation), info())
    assert placement.top_left == top_left
    assert placement.top_right == top_right
    assert placement.bottom_left == bottom_left
    assert placement.bounds == bounds


def test_rotation_keeps_the_south_west_corner_in_place():
    for rotation in (0, 90, 180, 270):
        bounds = place(Part("T-1", (15, -40), rotation), info()).bounds
        assert bounds[:2] == (15, -40)


def test_rotate_buttons_step_the_right_way():
    part = Part("T-1", (0, 0))
    part.rotate_clockwise()
    assert part.rotation == 270
    part.rotate_counter_clockwise()
    part.rotate_counter_clockwise()
    assert part.rotation == 90


def test_bad_rotation_is_refused():
    with pytest.raises(ValueError):
        place(Part("T-1", (0, 0), 45), info())


@pytest.mark.parametrize(
    "value, expected",
    [(0, 0), (2, 0), (2.5, 5), (3, 5), (7.4, 5), (-2, 0), (-2.5, 0), (-3, -5)],
)
def test_snap_rounds_like_javascript(value, expected):
    assert snap(value) == expected


def test_tonnage_adds_up_and_sorts_largest_first():
    library = {
        "A": info(uses={"Fuel": 8, "Cargo": 2, "Empty": 0}),
        "B": info(uses={"Cargo": 20}),
    }
    rows = tonnage([Part("A", (0, 0)), Part("B", (0, 0))], library)
    assert rows == [("Cargo", 22), ("Fuel", 8)]
    assert total_tons(rows) == 30


def test_a_part_placed_twice_counts_twice():
    library = {"A": info(uses={"Fuel": 8})}
    assert tonnage([Part("A", (0, 0)), Part("A", (5, 5))], library) == [("Fuel", 16)]


def test_percent_rounds_like_javascript():
    assert percent(1, 8) == 13  # 12.5 rounds up, where Python's round gives 12
    assert percent(0, 0) == 0


def test_filters_combine(library):
    fuel = filter_library(library, usages={"Fuel"})
    assert fuel and all(part.uses.get("Fuel") for part in fuel)

    both = filter_library(library, usages={"Fuel"}, shapes={"SS"})
    assert both and all(part.shape == "SS" for part in both)
    assert len(both) < len(fuel)


def test_no_filter_shows_the_whole_library(library):
    assert len(filter_library(library)) == len(library)


def test_ticking_two_usages_means_either(library):
    fuel = filter_library(library, usages={"Fuel"})
    cargo = filter_library(library, usages={"Cargo"})
    either = filter_library(library, usages={"Fuel", "Cargo"})
    assert len(either) >= max(len(fuel), len(cargo))


def test_every_filter_finds_something(library):
    from shipyard.model import USAGE_FILTERS, SHAPES

    for usage in USAGE_FILTERS:
        assert filter_library(library, usages={usage}), usage
    for shape in SHAPES:
        assert filter_library(library, shapes={shape}), shape


def test_search_matches_part_of_a_code(library):
    found = filter_library(library, text="ss-00")
    assert found and all("SS-00" in part.code for part in found)


def test_overlay_and_mirror_images():
    part = info(
        mirror_url="m.png", overlay_url="o.png", overlay_mirror_url="om.png"
    )
    assert part.image(False) == "T/T-1.png"
    assert part.image(True) == "m.png"
    assert part.overlay_image(True) == "om.png"
    assert part.overlay_image(False) == "o.png"


def test_mirror_flag_on_a_part_without_a_mirror_keeps_the_normal_image():
    assert info().image(True) == "T/T-1.png"


def test_save_uses_the_original_format(tmp_path, library):
    ship = Ship.single("Courier", [Part("SS-001", (10, -5), 90, overlay=True, mirror=True)])
    path = tmp_path / "ship.json"
    ship.save(path)
    assert json.loads(path.read_text()) == {
        "meta": SHIP_META,
        "name": "Courier",
        "bow": "left",
        "parts": [
            {
                "code": "SS-001",
                "corner": [10, -5],
                "rotation": 90,
                "overlay": True,
                "mirror": True,
            }
        ],
    }


def test_save_and_load_round_trip(tmp_path, library):
    ship = Ship.single("Iván's Boat", [Part("SS-001", (10, -5), 270, mirror=True)])
    path = tmp_path / "ship.json"
    ship.save(path)
    assert Ship.load(path, library) == ship


def test_loads_a_file_written_by_the_original(tmp_path, library):
    path = tmp_path / "web.json"
    path.write_text(
        '{"meta":"Traveller Geomorph Ship","name":"Web","parts":'
        '[{"code":"SS-001","corner":[60,-10],"rotation":90,"overlay":false}]}'
    )
    ship = Ship.load(path, library)
    assert ship.parts == [Part("SS-001", (60, -10), 90)]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"meta":"Something else","parts":[]}',
        '{"meta":"Traveller Geomorph Ship"}',
        '{"meta":"Traveller Geomorph Ship","parts":[{"code":"SS-001"}]}',
        '{"meta":"Traveller Geomorph Ship","parts":[{"corner":[0,0]}]}',
        '{"meta":"Traveller Geomorph Ship","parts":'
        '[{"code":"SS-001","corner":[0,0],"rotation":45}]}',
    ],
)
def test_bad_files_are_refused(tmp_path, library, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(ShipFileError):
        Ship.load(path, library)


def test_unknown_parts_are_named_not_dropped(tmp_path, library):
    path = tmp_path / "ship.json"
    path.write_text(
        '{"meta":"Traveller Geomorph Ship","parts":'
        '[{"code":"NOPE-1","corner":[0,0]}]}'
    )
    with pytest.raises(ShipFileError, match="NOPE-1"):
        Ship.load(path, library)


def test_suggested_filename_follows_the_original():
    ship = Ship.single("Courier/2", [])
    assert (
        suggested_filename(ship, 120, "2026-11-06", "json")
        == "geomorph-2026-11-06-120dTon-Courier-2.json"
    )


def test_loads_a_ship_generated_by_the_original_code(library):
    # Written by the original's rollShip, in the shape its saveAsJson uses.
    path = Path(__file__).parent / "data" / "from-original-app.json"
    raw = json.loads(path.read_text())
    ship = Ship.load(path, library)

    assert len(ship.parts) == len(raw["parts"])
    assert any(part.mirror for part in ship.parts)
    for before, after in zip(raw["parts"], ship.to_json()["parts"]):
        assert after["code"] == before["code"]
        assert after["corner"] == before["corner"]
        assert after["rotation"] == before["rotation"]
        assert after.get("mirror", False) == before.get("mirror", False)


def two_deck_ship():
    return Ship(
        "Courier",
        [
            Deck("Upper", [Part("SS-001", (0, 0), 90)]),
            Deck("Lower", [Part("SS-002", (5, 5)), Part("SS-003", (55, 5))]),
        ],
    )


def test_a_one_deck_ship_only_adds_the_bow_to_the_original_format():
    data = Ship.single("Plain", [Part("SS-001", (0, 0))]).to_json()
    assert set(data) == {"meta", "name", "bow", "parts"}
    assert "deck" not in data["parts"][0]


def test_decks_are_a_flat_part_list_with_a_deck_number():
    data = two_deck_ship().to_json()
    assert data["decks"] == [{"name": "Upper"}, {"name": "Lower"}]
    assert [part["deck"] for part in data["parts"]] == [0, 1, 1]
    assert data["parts"][1]["code"] == "SS-002"


def test_decks_survive_save_and_load(tmp_path, library):
    path = tmp_path / "ship.json"
    two_deck_ship().save(path)
    assert Ship.load(path, library) == two_deck_ship()


def test_an_empty_deck_survives_save_and_load(tmp_path, library):
    ship = two_deck_ship()
    ship.add_deck(2)
    path = tmp_path / "ship.json"
    ship.save(path)
    assert Ship.load(path, library) == ship


def test_one_renamed_deck_keeps_its_name(tmp_path, library):
    ship = Ship("Boat", [Deck("Main", [Part("SS-001", (0, 0))])])
    path = tmp_path / "ship.json"
    ship.save(path)
    assert Ship.load(path, library).decks[0].name == "Main"
    assert "deck" not in ship.to_json()["parts"][0]


def test_decks_rebuild_after_the_original_app_resaves_the_file(library):
    # The original keeps "deck" on each part but loses the top level list.
    data = two_deck_ship().to_json()
    del data["decks"]
    ship = Ship.from_json(data, library)
    assert [deck.name for deck in ship.decks] == ["Deck 1", "Deck 2"]
    assert [len(deck.parts) for deck in ship.decks] == [1, 2]


def test_a_huge_deck_number_makes_no_huge_ship(library):
    data = Ship.single("Boat", [Part("SS-001", (0, 0))]).to_json()
    data["parts"][0]["deck"] = 10**12
    assert len(Ship.from_json(data, library).decks) == 1


@pytest.mark.parametrize("change", [
    lambda data: data.update(decks="Upper"),
    lambda data: data.update(decks=[]),
    lambda data: data.update(decks=[{"title": "Upper"}]),
    lambda data: data["parts"][0].update(deck=7),
    lambda data: data["parts"][0].update(deck=-1),
    lambda data: data["parts"][0].update(deck="Upper"),
    lambda data: data["parts"][0].update(deck=True),
])
def test_bad_deck_data_is_refused(library, change):
    data = two_deck_ship().to_json()
    change(data)
    with pytest.raises(ShipFileError):
        Ship.from_json(data, library)


def test_new_decks_take_the_first_free_number():
    ship = Ship.empty()
    ship.add_deck(1)
    ship.add_deck(2)
    assert [deck.name for deck in ship.decks] == ["Deck 1", "Deck 2", "Deck 3"]
    ship.delete_deck(1)
    assert ship.add_deck(2).name == "Deck 2"


def test_duplicating_a_deck_copies_its_parts():
    ship = two_deck_ship()
    copy = ship.duplicate_deck(0)
    assert ship.decks[1] is copy
    assert copy.name == "Upper copy"
    assert copy.parts == ship.decks[0].parts
    copy.parts[0].rotation = 180
    assert ship.decks[0].parts[0].rotation == 90


def test_the_last_deck_cannot_be_deleted():
    with pytest.raises(ValueError):
        Ship.empty().delete_deck(0)


def test_reordering_decks():
    ship = two_deck_ship()
    ship.reorder_deck(0, 1)
    assert [deck.name for deck in ship.decks] == ["Lower", "Upper"]


def test_ship_parts_and_tonnage_cover_every_deck(library):
    ship = two_deck_ship()
    assert len(ship.parts) == 3
    assert model_total(ship, library) == 24


def model_total(ship, library):
    return total_tons(tonnage(ship.parts, library))


def test_filename_can_name_a_deck():
    ship = Ship.single("Boat", [])
    assert (
        suggested_filename(ship, 8, "2026-11-06", "png", deck="Upper")
        == "geomorph-2026-11-06-8dTon-Boat-Upper.png"
    )


def test_group_bounds_cover_every_part(library):
    parts = [Part("SS-001", (0, 0)), Part("SS-002", (100, 50))]
    assert group_bounds(parts, library) == (0, 0, 150, 100)


def test_copies_keep_their_layout_and_settings(library):
    parts = [Part("SS-001", (0, 0), 90, True, True), Part("SS-002", (50, 0))]
    copies = copies_centred_on(parts, library, (200, 300))
    assert group_bounds(copies, library) == (150, 275, 250, 325)
    assert copies[1].corner[0] - copies[0].corner[0] == 50
    assert copies[0].rotation == 90 and copies[0].overlay and copies[0].mirror


def test_copies_are_separate_from_the_originals(library):
    parts = [Part("SS-001", (0, 0))]
    copies = copies_centred_on(parts, library, (0, 0))
    copies[0].rotation = 180
    assert parts[0].rotation == 0


def test_copies_land_on_the_grid(library):
    parts = [Part("SS-001", (0, 0))]
    copies = copies_centred_on(parts, library, (2, 3))
    assert copies[0].corner[0] % 5 == 0 and copies[0].corner[1] % 5 == 0


@pytest.mark.parametrize(
    "bow, up, right, down, left",
    [
        ("up", "Bow", "Starboard", "Stern", "Port"),
        ("right", "Port", "Bow", "Starboard", "Stern"),
        ("down", "Stern", "Port", "Bow", "Starboard"),
        ("left", "Starboard", "Stern", "Port", "Bow"),
    ],
)
def test_sides_follow_the_bow(bow, up, right, down, left):
    assert sides_for_bow(bow) == {
        "up": up,
        "right": right,
        "down": down,
        "left": left,
    }


def test_port_is_on_the_left_when_facing_the_bow():
    # Facing up the map, port is the left hand, the rule the labels rest on.
    sides = sides_for_bow("up")
    assert sides["left"] == "Port"
    assert sides["right"] == "Starboard"


def test_turning_the_bow_wraps_around():
    assert turn_bow("up", 1) == "right"
    assert turn_bow("left", 1) == "up"
    assert turn_bow("up", -1) == "left"
    assert turn_bow("right", 4) == "right"


@pytest.mark.parametrize("bow", ["up", "right", "down", "left"])
def test_bow_survives_save_and_load(tmp_path, library, bow):
    ship = Ship("Boat", [Deck("Deck 1", [Part("SS-001", (0, 0))])], bow)
    path = tmp_path / "ship.json"
    ship.save(path)
    assert json.loads(path.read_text())["bow"] == bow
    assert Ship.load(path, library).bow == bow


def test_a_file_from_the_original_gets_the_default_bow(library):
    path = Path(__file__).parent / "data" / "from-original-app.json"
    assert "bow" not in json.loads(path.read_text())
    assert Ship.load(path, library).bow == "left"


@pytest.mark.parametrize("bow", ["north", "", 90, None, ["up"]])
def test_a_bad_bow_is_refused(library, bow):
    data = Ship.empty().to_json()
    data["bow"] = bow
    with pytest.raises(ShipFileError):
        Ship.from_json(data, library)


def test_a_new_ship_has_the_default_bow():
    assert Ship.empty().bow == "left"


def test_starboard_tiles_sit_on_the_starboard_side_of_a_default_ship(library):
    # The original's ships put the plain tile on top and the flipped one below.
    # Plain wing images are the starboard ones, so top must read Starboard.
    path = Path(__file__).parent / "data" / "from-original-app.json"
    ship = Ship.load(path, library)
    plain = [p for p in ship.parts if p.code.startswith("LS") and not p.mirror]
    flipped = [p for p in ship.parts if p.code.startswith("LS") and p.mirror]
    if plain and flipped:
        assert "Starboard" in library[plain[0].code].url
        assert plain[0].corner[1] > flipped[0].corner[1]
    assert sides_for_bow(ship.bow)["up"] == "Starboard"


def ordering_library():
    def part(code, **uses):
        return code, info(code=code, uses=uses)

    return dict(
        [
            part("MIX-2", Engineering=5, Cargo=5, Fuel=5),
            part("AND-LOUNGE", Engineering=5, Lounge=5),
            part("ONLY-B", Engineering=5),
            part("AND-CARGO", Engineering=5, Cargo=5),
            part("FUEL-ONLY", Fuel=5),
            part("ONLY-A", Engineering=9),
            part("EMPTY-USE", Engineering=5, Fuel=0),
            part("BOTH", Engineering=5, Fuel=5),
        ]
    )


def codes(parts):
    return [part.code for part in parts]


def test_parts_with_only_the_ticked_use_come_first():
    found = codes(filter_library(ordering_library(), usages={"Engineering"}))
    assert found[:3] == ["ONLY-B", "ONLY-A", "EMPTY-USE"]


def test_then_one_other_type_sorted_by_that_type_then_two():
    found = codes(filter_library(ordering_library(), usages={"Engineering"}))
    assert found[3:] == ["AND-CARGO", "BOTH", "AND-LOUNGE", "MIX-2"]


def test_a_use_of_zero_tons_does_not_count_as_another_type():
    found = codes(filter_library(ordering_library(), usages={"Engineering"}))
    assert found.index("EMPTY-USE") < found.index("AND-CARGO")


def test_with_two_filters_neither_counts_as_other():
    found = codes(
        filter_library(ordering_library(), usages={"Engineering", "Fuel"})
    )
    assert found[0] == "BOTH"
    assert set(found[1:5]) == {"ONLY-B", "ONLY-A", "EMPTY-USE", "FUEL-ONLY"}
    assert found.index("MIX-2") < found.index("AND-LOUNGE")
    assert found.index("AND-CARGO") < found.index("AND-LOUNGE")


def test_spelling_variants_are_not_counted_as_other_types():
    library = {
        "A": info(code="A", uses={"Stables": 5}),
        "B": info(code="B", uses={"Stable": 5, "Fuel": 5}),
    }
    assert codes(filter_library(library, usages={"Stable"})) == ["A", "B"]


def test_shape_and_search_leave_the_order_alone(library):
    in_order = [part.code for part in library.values() if part.shape == "SS"]
    assert codes(filter_library(library, shapes={"SS"})) == in_order
    found = codes(filter_library(library, text="SS-"))
    assert found == [code for code in library if "ss-" in code.casefold()]


def test_turning_filters_off_restores_library_order(library):
    assert codes(filter_library(library)) == list(library)


def test_real_engineering_parts_run_from_specialist_to_general(library):
    found = filter_library(library, usages={"Engineering"})
    counts = [
        len([use for use, tons in part.uses.items() if tons and use != "Engineering"])
        for part in found
    ]
    assert counts == sorted(counts)
    assert counts[0] == 0
