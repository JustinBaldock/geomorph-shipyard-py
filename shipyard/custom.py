"""Where cut subparts and custom library parts live on disk.

Everything here sits under assets/, which git ignores, because anything cut
from the geomorphs is still under their CC BY-NC licence.

    assets/geomorph-hires-subpart/
        <name>.png              a cut subpart
        <name>.json             where it was cut from
    assets/geomorphs-highres-custom/
        <code>.png              a finished part at high resolution
        <code>-flipped.png      optional, what Flip shows
        <code>-cargo.png        optional, what Show cargo adds
        <code>-flipped-cargo.png  optional, Show cargo on a flipped part
        <code>.json             its library entry, in the same layout as the
                                original project's parts data
        lowres/                 the same images at the size the map uses

The PNG files decide which parts exist. A PNG dropped into the folder becomes
a part, and gets a JSON file written for it. Deleting a part's PNG removes it,
and a JSON file with no matching PNG is ignored. Renaming a PNG and its JSON
together keeps the part working.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBPART_FOLDER = REPO_ROOT / "assets" / "geomorph-hires-subpart"
CUSTOM_FOLDER = REPO_ROOT / "assets" / "geomorphs-highres-custom"
# Parts used to share this one file. It is ignored if it is still there.
OLD_PARTS_FILE_NAME = "custom-parts.json"
REQUIRED_FIELDS = ("url", "sizeX", "sizeY", "tons", "uses", "facilities")
EXTRA_IMAGE_FIELDS = ("mirrorUrl", "overlayUrl", "overlayMirrorUrl")
IMAGE_FIELDS = ("url", *EXTRA_IMAGE_FIELDS)
# Images that belong to a part, and aren't parts of their own.
EXTRA_IMAGE_SUFFIXES = ("-flipped-cargo", "-flipped", "-cargo")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LOW_RES_FOLDER_NAME = "lowres"

# The high-res tiles hold 300 pixels per square, and the low-res ones 60.
PIXELS_PER_SQUARE = 300
LOW_RES_SCALE = 0.2
GUTTER_SQUARES = 2
SQUARES_PER_DTON = 2


def safe_name(text: str) -> str:
    """Reduce text to something that is safe as a file name."""
    cleaned = re.sub(r"[^\w\- ]+", "-", text.strip())
    return re.sub(r"[\s-]*-[\s-]*", "-", cleaned).strip(" -")


def entry_path(code: str, folder: Path = CUSTOM_FOLDER) -> Path:
    return Path(folder) / f"{code}.json"


def png_size(path: Path) -> tuple[int, int] | None:
    """A PNG's size from its header, or None if it isn't a readable PNG."""
    try:
        with open(path, "rb") as file:
            header = file.read(24)
    except OSError:
        return None
    if len(header) < 24 or not header.startswith(PNG_SIGNATURE):
        return None
    return struct.unpack(">II", header[16:24])


def squares_for(name: str, width: int, height: int) -> tuple[int, int]:
    """A new part's size: from a name like CU-003-20x20, or else the image."""
    named = re.search(r"(?<![\d.])(\d+)x(\d+)(?![\d.])", name)
    if named and int(named[1]) and int(named[2]):
        return int(named[1]), int(named[2])
    border = 2 * GUTTER_SQUARES
    return (
        max(1, round(width / PIXELS_PER_SQUARE) - border),
        max(1, round(height / PIXELS_PER_SQUARE) - border),
    )


def new_entry(path: Path) -> dict | None:
    """A library entry for a PNG that came from outside the editor."""
    size = png_size(path)
    if size is None:
        return None
    squares_x, squares_y = squares_for(path.stem, *size)
    return {
        "url": path.name,
        "sizeX": squares_x,
        "sizeY": squares_y,
        "tons": squares_x * squares_y // SQUARES_PER_DTON,
        "uses": {},
        "facilities": {},
        "layout": [
            {"kind": "image", "file": path.name, "x": 0, "y": 0, "turns": 0, "mirror": False}
        ],
    }


def _read_entry(path: Path) -> dict | None:
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        return entry if all(field in entry for field in REQUIRED_FIELDS) else None
    except (OSError, TypeError, ValueError):
        return None


def _is_extra_image(path: Path, stems: set[str], claimed: set[str]) -> bool:
    if path.name in claimed:
        return True
    return any(
        path.stem.endswith(suffix) and path.stem[: -len(suffix)] in stems
        for suffix in EXTRA_IMAGE_SUFFIXES
    )


def _matched_to_files(entry: dict, png: Path) -> dict:
    """An entry updated to match what's really in the folder."""
    matched = {**entry, "url": png.name}
    for field in EXTRA_IMAGE_FIELDS:
        if field in matched and not (png.parent / matched[field]).is_file():
            del matched[field]
    return matched


def load_entries(
    folder: Path = None, problems: list[str] | None = None
) -> dict:
    """The custom library entries, by code, from the files in the folder.

    Each PNG is a part. One with no JSON file gets one written for it. A
    damaged file only loses its own part, and is never overwritten. Its name
    is added to `problems`, so the app can say which file needs attention.
    """
    folder = Path(CUSTOM_FOLDER if folder is None else folder)
    entries: dict = {}
    if not folder.is_dir():
        return entries
    problems = [] if problems is None else problems

    saved: dict = {}
    damaged: set[str] = set()
    for path in sorted(folder.glob("*.json")):
        if path.name == OLD_PARTS_FILE_NAME:
            continue
        entry = _read_entry(path)
        if entry is None:
            damaged.add(path.stem)
            problems.append(path.name)
        else:
            saved[path.stem] = entry

    pngs = sorted(folder.glob("*.png"))
    stems = {png.stem for png in pngs}
    claimed = {
        entry[field]
        for entry in saved.values()
        for field in EXTRA_IMAGE_FIELDS
        if field in entry
    }
    for png in pngs:
        code = png.stem
        if code in damaged or _is_extra_image(png, stems, claimed):
            continue
        entry = saved.get(code)
        if entry is None:
            entry = new_entry(png)
            if entry is None:
                problems.append(png.name)
                continue
            save_entry(code, entry, folder)
        entries[code] = _matched_to_files(entry, png)
    return entries


def _write_json(path: Path, data: dict) -> None:
    # Written beside the target first, so a failed save can't lose the file.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def save_entry(code: str, entry: dict, folder: Path = None) -> None:
    """Write one part's JSON file, leaving every other part alone."""
    folder = Path(CUSTOM_FOLDER if folder is None else folder)
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(entry_path(code, folder), entry)


def next_code(taken) -> str:
    """The first free code in the CU-001 series."""
    number = 1
    while f"CU-{number:03d}" in taken:
        number += 1
    return f"CU-{number:03d}"


def image_paths(
    code: str, folder: Path = CUSTOM_FOLDER, suffix: str = ""
) -> tuple[Path, Path]:
    """Where a part's high-res and low-res images go.

    `suffix` picks one of its extra images, such as `-flipped`.
    """
    folder = Path(folder)
    name = f"{code}{suffix}.png"
    return folder / name, folder / LOW_RES_FOLDER_NAME / name


def subpart_names(folder: Path = SUBPART_FOLDER) -> list[str]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.png"))


def subpart_path(name: str, folder: Path = SUBPART_FOLDER) -> Path:
    return Path(folder) / f"{name}.png"


def subpart_categories(name: str, folder: Path = SUBPART_FOLDER) -> list[str]:
    """The categories ticked for a subpart. Older cuts don't have any."""
    path = Path(folder) / f"{name}.json"
    try:
        categories = json.loads(path.read_text(encoding="utf-8")).get("categories", [])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return []
    return categories if isinstance(categories, list) else []


def save_subpart_record(name: str, record: dict, folder: Path = SUBPART_FOLDER) -> None:
    """Note where a subpart came from, so its credit can be traced later."""
    _write_json(Path(folder) / f"{name}.json", record)
