"""Where cut subparts and custom library parts live on disk.

Everything here sits under assets/, which git ignores, because anything cut
from the geomorphs is still under their CC BY-NC licence.

    assets/geomorph-hires-subpart/
        <name>.png              a cut subpart
        <name>.json             where it was cut from
    assets/geomorphs-highres-custom/
        <code>.png              a finished part at high resolution
        lowres/<code>.png       the same part at the size the map uses
        custom-parts.json       the library entries, in the same layout as
                                the original project's parts data
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBPART_FOLDER = REPO_ROOT / "assets" / "geomorph-hires-subpart"
CUSTOM_FOLDER = REPO_ROOT / "assets" / "geomorphs-highres-custom"
CUSTOM_FILE_NAME = "custom-parts.json"
LOW_RES_FOLDER_NAME = "lowres"

# The high-res tiles hold 300 pixels per square, and the low-res ones 60.
PIXELS_PER_SQUARE = 300
LOW_RES_SCALE = 0.2
GUTTER_SQUARES = 2
SQUARES_PER_DTON = 2


class CustomFileError(ValueError):
    """The custom parts file can't be used. The message is for the user."""


def safe_name(text: str) -> str:
    """Reduce text to something that is safe as a file name."""
    cleaned = re.sub(r"[^\w\- ]+", "-", text.strip())
    return re.sub(r"[\s-]*-[\s-]*", "-", cleaned).strip(" -")


def load_entries(folder: Path = CUSTOM_FOLDER) -> dict:
    """The custom library entries, by code. No file yet means no entries."""
    path = Path(folder) / CUSTOM_FILE_NAME
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CustomFileError(f"Could not read {path.name}: {error}") from error
    if not isinstance(entries, dict):
        raise CustomFileError(f"{path.name} is not a list of parts.")
    return entries


def _write_json(path: Path, data: dict) -> None:
    # Written beside the target first, so a failed save can't lose the file.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def save_entry(code: str, entry: dict, folder: Path = CUSTOM_FOLDER) -> None:
    entries = load_entries(folder)
    entries[code] = entry
    Path(folder).mkdir(parents=True, exist_ok=True)
    _write_json(Path(folder) / CUSTOM_FILE_NAME, entries)


def next_code(taken) -> str:
    """The first free code in the CU-001 series."""
    number = 1
    while f"CU-{number:03d}" in taken:
        number += 1
    return f"CU-{number:03d}"


def image_paths(code: str, folder: Path = CUSTOM_FOLDER) -> tuple[Path, Path]:
    """Where a part's high-res and low-res images go."""
    folder = Path(folder)
    return folder / f"{code}.png", folder / LOW_RES_FOLDER_NAME / f"{code}.png"


def subpart_names(folder: Path = SUBPART_FOLDER) -> list[str]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.png"))


def subpart_path(name: str, folder: Path = SUBPART_FOLDER) -> Path:
    return Path(folder) / f"{name}.png"


def save_subpart_record(name: str, record: dict, folder: Path = SUBPART_FOLDER) -> None:
    """Note where a subpart came from, so its credit can be traced later."""
    _write_json(Path(folder) / f"{name}.json", record)
