"""Ships, parts and the rules that apply to them.

There is no Qt in this file, so all of it can be tested without a window.

Positions use the same "map units" as the original web app. One map square
is 5 units, x grows to the east and y grows to the north. Ship files are
interchangeable with the original app for that reason.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

UNITS_PER_SQUARE = 5
SNAP_UNITS = 5
# Every geomorph image has a two-square border around the part itself.
IMAGE_MARGIN_UNITS = 10

SHIP_META = "Traveller Geomorph Ship"
DEFAULT_SHIP_NAME = "YourShipNameGoesHere"
FIRST_DECK_NAME = "Deck 1"

# Same order as parts_library.js in the original project.
SHAPES = (
    "HG", "LG", "QG", "Sh", "SEF", "SEFW", "SEA", "SEAW", "LC", "SC", "HC",
    "LS", "SS", "AO", "M", "TG", "DV", "As", "UG", "core", "edge", "corner",
    "end",
)

SHAPE_NAMES = {
    "HG": "Half geomorphs",
    "LG": "Long geomorphs",
    "QG": "Quarter geomorphs",
    "Sh": "Short geomorphs",
    "SEF": "Short end, fore",
    "SEFW": "Short end, fore wrappers",
    "SEA": "Short end, aft",
    "SEAW": "Short end, aft wrappers",
    "LC": "Long cores",
    "SC": "Short cores",
    "HC": "Half cores",
    "LS": "Long sides",
    "SS": "Short sides",
    "AO": "Add-ons",
    "M": "Miscellaneous",
    "TG": "Transition",
    "DV": "Dorsal and ventral",
    "As": "Asteroid hulls",
    "UG": "Unique hulls",
    "core": "Core (1st gen)",
    "edge": "Edge (1st gen)",
    "corner": "Corner (1st gen)",
    "end": "End (1st gen)",
}

# Same order as the usage filters in the original project.
USAGE_FILTERS = (
    "Engineering", "Fuel", "Bridge", "Staterooms", "Seating", "Low Berths",
    "Barracks", "Brig", "Lounge", "Cargo", "Liquid", "Stable", "Mining",
    "Military", "Armory", "Vehicle", "Hangar", "Docking", "Drones", "Sensors",
    "Comms", "Airlock", "Labs", "Medical", "Biosphere", "Training",
    "Briefing", "Turret", "Weapons",
)

# The parts data spells some uses more than one way. A filter matches all of
# the spellings, so that ticking "Stable" also finds the "Stables" parts.
_USAGE_ALIASES = {
    "Stable": ("Stables",),
    "Labs": ("Lab",),
    "Airlock": ("Airlocks",),
    "Vehicle": ("Vehicles",),
    "Low Berths": ("Low Berth",),
    "Cargo": ("External Cargo",),
}


class ShipFileError(ValueError):
    """A ship file could not be read. The message is shown to the user."""


def js_round(value: float) -> int:
    """Round halves up, as JavaScript's Math.round does."""
    return math.floor(value + 0.5)


def snap(value: float) -> int:
    return js_round(value / SNAP_UNITS) * SNAP_UNITS


@dataclass(frozen=True)
class PartInfo:
    """One entry in the parts library."""

    code: str
    shape: str
    size_x: int
    size_y: int
    tons: int
    uses: dict
    facilities: dict
    url: str
    mirror_url: str | None = None
    overlay_url: str | None = None
    overlay_mirror_url: str | None = None

    @property
    def has_mirror(self) -> bool:
        return bool(self.mirror_url)

    def image(self, mirror: bool) -> str:
        return (self.mirror_url or self.url) if mirror else self.url

    def overlay_image(self, mirror: bool) -> str | None:
        return self.overlay_mirror_url if mirror else self.overlay_url

    def summary(self) -> str:
        uses = ", ".join(sorted(use for use, tons in self.uses.items() if tons))
        return f"{self.size_x}×{self.size_y}, {self.tons} dTon" + (
            f", {uses}" if uses else ""
        )


def _clean_url(url: str | None) -> str | None:
    """Reduce an image path to its place inside the geomorphs folder."""
    if not url:
        return None
    while url.startswith("../"):
        url = url[3:]
    return url.removeprefix("geomorphs/")


def load_library(data_dir: Path) -> dict[str, PartInfo]:
    """Read the parts data, keeping the original's order.

    When a code appears twice the later entry wins, as in the original.
    """
    parts: dict[str, PartInfo] = {}
    for shape in SHAPES:
        with open(data_dir / f"{shape}.json", encoding="utf-8") as file:
            entries = json.load(file)
        for code, entry in entries.items():
            parts[code] = PartInfo(
                code=code,
                shape=shape,
                size_x=entry["sizeX"],
                size_y=entry["sizeY"],
                tons=entry["tons"],
                uses=entry["uses"],
                facilities=entry["facilities"],
                url=_clean_url(entry["url"]),
                mirror_url=_clean_url(entry.get("mirrorUrl")),
                overlay_url=_clean_url(entry.get("overlayUrl")),
                overlay_mirror_url=_clean_url(entry.get("overlayMirrorUrl")),
            )
    return parts


def _matches_usage(info: PartInfo, usage: str) -> bool:
    if usage == "Turret":
        # No part lists Turret as a use, they are counted as facilities.
        return bool(
            info.facilities.get("Turret") or info.facilities.get("Turrets")
        )
    names = (usage, *_USAGE_ALIASES.get(usage, ()))
    return any(info.uses.get(name) for name in names)


def filter_library(
    library: dict[str, PartInfo],
    usages: set[str] = frozenset(),
    shapes: set[str] = frozenset(),
    text: str = "",
) -> list[PartInfo]:
    """Parts matching any ticked usage and any ticked shape.

    Ticking nothing in a group means no limit from that group.
    """
    text = text.strip().casefold()
    return [
        info
        for info in library.values()
        if (not usages or any(_matches_usage(info, u) for u in usages))
        and (not shapes or info.shape in shapes)
        and (not text or text in info.code.casefold())
    ]


@dataclass
class Part:
    """A part placed on a ship. `corner` is its south-west corner."""

    code: str
    corner: tuple[int, int]
    rotation: int = 0
    overlay: bool = False
    mirror: bool = False

    def rotate_clockwise(self) -> None:
        self.rotation = (self.rotation + 270) % 360

    def rotate_counter_clockwise(self) -> None:
        self.rotation = (self.rotation + 90) % 360

    def move(self, dx: int, dy: int) -> None:
        self.corner = (self.corner[0] + dx, self.corner[1] + dy)

    def to_json(self) -> dict:
        data = {
            "code": self.code,
            "corner": list(self.corner),
            "rotation": self.rotation,
            "overlay": self.overlay,
        }
        if self.mirror:
            data["mirror"] = True
        return data


@dataclass(frozen=True)
class Placement:
    """Where a part lands on the map, in map units.

    The three image corners say how the image is laid onto the map, and
    together they carry its rotation. `bounds` is the part itself, without the
    image's border, as (west, south, east, north).
    """

    top_left: tuple[int, int]
    top_right: tuple[int, int]
    bottom_left: tuple[int, int]
    bounds: tuple[int, int, int, int]


def place(part: Part, info: PartInfo) -> Placement:
    x1 = part.corner[0] - IMAGE_MARGIN_UNITS
    y1 = part.corner[1] - IMAGE_MARGIN_UNITS
    length = (info.size_y + 4) * UNITS_PER_SQUARE
    width = (info.size_x + 4) * UNITS_PER_SQUARE
    margin = IMAGE_MARGIN_UNITS
    rotation = part.rotation % 360

    upright = (x1 + margin, y1 + margin, x1 + width - margin, y1 + length - margin)
    sideways = (x1 + margin, y1 + margin, x1 + length - margin, y1 + width - margin)

    if rotation == 0:
        return Placement(
            (x1, y1 + length), (x1 + width, y1 + length), (x1, y1), upright
        )
    if rotation == 90:
        return Placement((x1, y1), (x1, y1 + width), (x1 + length, y1), sideways)
    if rotation == 180:
        return Placement(
            (x1 + width, y1), (x1, y1), (x1 + width, y1 + length), upright
        )
    if rotation == 270:
        return Placement(
            (x1 + length, y1 + width),
            (x1 + length, y1),
            (x1, y1 + width),
            sideways,
        )
    raise ValueError(f"Rotation must be a multiple of 90, got {part.rotation}")


def group_bounds(
    parts: list[Part], library: dict[str, PartInfo]
) -> tuple[int, int, int, int]:
    """The box around some parts, as (west, south, east, north)."""
    boxes = [place(part, library[part.code]).bounds for part in parts]
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def copies_centred_on(
    parts: list[Part], library: dict[str, PartInfo], centre: tuple[int, int]
) -> list[Part]:
    """Copies of parts moved as one so the group is centred on a map point."""
    west, south, east, north = group_bounds(parts, library)
    dx = snap(centre[0] - (west + east) / 2)
    dy = snap(centre[1] - (south + north) / 2)
    return [
        replace(part, corner=(part.corner[0] + dx, part.corner[1] + dy))
        for part in parts
    ]


def tonnage(parts: list[Part], library: dict[str, PartInfo]) -> list[tuple[str, float]]:
    """Tons per use, largest first, leaving out uses that come to zero."""
    totals: dict[str, float] = {}
    for part in parts:
        for use, tons in library[part.code].uses.items():
            totals[use] = totals.get(use, 0) + tons
    rows = [(use, tons) for use, tons in totals.items() if tons]
    return sorted(rows, key=lambda row: row[1], reverse=True)


def total_tons(rows: list[tuple[str, float]]) -> float:
    return sum(tons for _, tons in rows)


def percent(tons: float, total: float) -> int:
    return js_round(100 * tons / total) if total else 0


def format_tons(tons: float) -> str:
    return str(int(tons)) if float(tons).is_integer() else f"{tons:g}"


@dataclass
class Deck:
    """One level of a ship, with its own parts."""

    name: str
    parts: list[Part] = field(default_factory=list)


def _bad_file() -> ShipFileError:
    return ShipFileError("This file does not seem to be a Traveller Geomorph Ship.")


def _deck_names(data: dict) -> list[str] | None:
    """The deck names from a ship file, or None if it doesn't list them."""
    if "decks" not in data:
        return None
    decks = data["decks"]
    if not isinstance(decks, list) or not decks:
        raise _bad_file()
    names = []
    for deck in decks:
        if not isinstance(deck, dict) or not isinstance(deck.get("name"), str):
            raise _bad_file()
        names.append(deck["name"])
    return names


def _deck_number(entry: dict) -> int:
    number = entry.get("deck", 0)
    if not isinstance(number, int) or isinstance(number, bool) or number < 0:
        raise _bad_file()
    return number


@dataclass
class Ship:
    """A ship is one or more decks.

    In a file the parts stay in one flat list, as in the original app, and
    each part carries the number of its deck. The original keeps fields it
    doesn't know, so it can open these ships, showing all the decks at once.
    A ship with one plain deck is written exactly as the original writes it.
    """

    name: str
    decks: list[Deck]

    @classmethod
    def single(cls, name: str, parts: list[Part]) -> "Ship":
        return cls(name, [Deck(FIRST_DECK_NAME, parts)])

    @classmethod
    def empty(cls) -> "Ship":
        return cls.single(DEFAULT_SHIP_NAME, [])

    @property
    def parts(self) -> list[Part]:
        """Every part on every deck."""
        return [part for deck in self.decks for part in deck.parts]

    def next_deck_name(self) -> str:
        names = {deck.name for deck in self.decks}
        number = 1
        while f"Deck {number}" in names:
            number += 1
        return f"Deck {number}"

    def add_deck(self, index: int) -> Deck:
        deck = Deck(self.next_deck_name())
        self.decks.insert(index, deck)
        return deck

    def duplicate_deck(self, index: int) -> Deck:
        original = self.decks[index]
        deck = Deck(
            f"{original.name} copy", [replace(part) for part in original.parts]
        )
        self.decks.insert(index + 1, deck)
        return deck

    def delete_deck(self, index: int) -> None:
        if len(self.decks) == 1:
            raise ValueError("A ship must keep at least one deck")
        del self.decks[index]

    def reorder_deck(self, source: int, destination: int) -> None:
        self.decks.insert(destination, self.decks.pop(source))

    def to_json(self) -> dict:
        several = len(self.decks) > 1
        parts = []
        for number, deck in enumerate(self.decks):
            for part in deck.parts:
                entry = part.to_json()
                if several:
                    entry["deck"] = number
                parts.append(entry)

        data = {"meta": SHIP_META, "name": self.name}
        if several or self.decks[0].name != FIRST_DECK_NAME:
            data["decks"] = [{"name": deck.name} for deck in self.decks]
        data["parts"] = parts
        return data

    def save(self, path: Path) -> None:
        text = json.dumps(self.to_json(), ensure_ascii=False, indent=2)
        Path(path).write_text(text + "\n", encoding="utf-8")

    @classmethod
    def from_json(cls, data, library: dict[str, PartInfo]) -> "Ship":
        if (
            not isinstance(data, dict)
            or data.get("meta") != SHIP_META
            or not isinstance(data.get("parts"), list)
        ):
            raise _bad_file()

        placed = []
        unknown = []
        for entry in data["parts"]:
            try:
                code = entry["code"]
                x, y = entry["corner"]
                rotation = entry.get("rotation", 0)
                if rotation % 90:
                    raise ValueError
                number = _deck_number(entry)
                if code in library:
                    part = Part(
                        code=code,
                        corner=(x, y),
                        rotation=rotation % 360,
                        overlay=bool(entry.get("overlay")),
                        mirror=bool(entry.get("mirror")),
                    )
                    placed.append((number, part))
                else:
                    unknown.append(str(code))
            except (KeyError, TypeError, ValueError, AttributeError) as error:
                raise _bad_file() from error

        if unknown:
            # Refused rather than dropped, so a save can't quietly lose parts.
            raise ShipFileError(
                "This ship uses parts that are not in the library: "
                + ", ".join(sorted(set(unknown)))
            )

        names = _deck_names(data)
        if names is None:
            # The original drops the deck list when it saves, but keeps the
            # number on each part, so the decks can be rebuilt from those.
            numbers = sorted({number for number, _ in placed}) or [0]
            positions = {number: i for i, number in enumerate(numbers)}
            decks = [Deck(f"Deck {i + 1}") for i in range(len(numbers))]
        else:
            positions = {i: i for i in range(len(names))}
            decks = [Deck(name) for name in names]

        for number, part in placed:
            if number not in positions:
                raise _bad_file()
            decks[positions[number]].parts.append(part)
        return cls(data.get("name") or DEFAULT_SHIP_NAME, decks)

    @classmethod
    def load(cls, path: Path, library: dict[str, PartInfo]) -> "Ship":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as error:
            raise ShipFileError(f"Could not read the file: {error}") from error
        except json.JSONDecodeError as error:
            raise ShipFileError("This file is not valid JSON.") from error
        return cls.from_json(data, library)


def suggested_filename(
    ship: Ship, tons: float, day: str, extension: str, deck: str | None = None
) -> str:
    """The original's file name pattern, without characters a path can't hold."""
    name = ship.name if deck is None else f"{ship.name}-{deck}"
    name = name.replace("/", "-").replace(":", "-")
    return f"geomorph-{day}-{format_tons(tons)}dTon-{name}.{extension}"
