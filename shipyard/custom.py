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

Each part has its own JSON file, and the app finds parts by looking in the
folder. Deleting a part's high-res PNG removes it from the library, and a JSON
file with no matching image is ignored.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBPART_FOLDER = REPO_ROOT / "assets" / "geomorph-hires-subpart"
CUSTOM_FOLDER = REPO_ROOT / "assets" / "geomorphs-highres-custom"
# Parts used to share this one file. It is ignored if it is still there.
OLD_PARTS_FILE_NAME = "custom-parts.json"
REQUIRED_FIELDS = ("url", "sizeX", "sizeY", "tons", "uses", "facilities")
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


def load_entries(
    folder: Path = CUSTOM_FOLDER, problems: list[str] | None = None
) -> dict:
    """The custom library entries, by code, from the files in the folder.

    A damaged file only loses its own part. Its name is added to `problems`,
    so the app can say which file needs attention.
    """
    folder = Path(folder)
    entries: dict = {}
    if not folder.is_dir():
        return entries
    for path in sorted(folder.glob("*.json")):
        if path.name == OLD_PARTS_FILE_NAME:
            continue
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if not all(field in entry for field in REQUIRED_FIELDS):
                raise ValueError
        except (OSError, TypeError, ValueError):
            if problems is not None:
                problems.append(path.name)
            continue
        if (folder / entry["url"]).is_file():
            entries[path.stem] = entry
    return entries


def _write_json(path: Path, data: dict) -> None:
    # Written beside the target first, so a failed save can't lose the file.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def save_entry(code: str, entry: dict, folder: Path = CUSTOM_FOLDER) -> None:
    """Write one part's JSON file, leaving every other part alone."""
    Path(folder).mkdir(parents=True, exist_ok=True)
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
