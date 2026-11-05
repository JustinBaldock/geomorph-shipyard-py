#!/usr/bin/env python3
"""Download the geomorph images into the assets folder.

A Python version of populate_geomorphs.js from the original project. It takes
the zip files from Eric B. Smith's Traveller Geomorphs site and copies out the
images the parts data asks for. It needs curl, but not the app's dependencies.

    python3 tools/fetch_geomorphs.py              # low-res, about 108 MB
    python3 tools/fetch_geomorphs.py --high-res   # high-res, about 231 MB
    python3 tools/fetch_geomorphs.py --force      # download the zips again

The images are CC BY-NC 4.0, by Robert Pearce and Eric B. Smith.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "shipyard" / "data"
TARGET_DIRS = {
    "lowRes": ROOT / "assets" / "geomorphs",
    "highRes": ROOT / "assets" / "geomorphs-highres",
}
ZIPS_DIR = ROOT / "geomorphs-source" / "zips"

BASE_URLS = (
    "https://gurpsland.no-ip.org/zip/Geomorphs",
    "https://gurpsland.sytes.net/zip/Geomorphs",
)

# Each entry is (zip file, folder its contents are known by in the mappings).
ZIPS = {
    "lowRes": (
        ("Geomorphs.zip", "Geomorphs"),
        ("CustomTiles.zip", "CustomTiles"),
        ("AdventureClass.zip", "AdventureClass"),
    ),
    "highRes": (
        ("GeomorphsHighRes.zip", "Geomorphs"),
        ("CustomTilesHighRes.zip", "CustomTiles"),
        ("AdventureClassHighRes1.zip", "AdventureClass"),
        ("AdventureClassHighRes2.zip", "AdventureClass"),
    ),
}

IMAGE_FIELDS = ("url", "mirrorUrl", "overlayUrl", "overlayMirrorUrl")


def required_images(data_dir: Path = DATA_DIR) -> set[str]:
    """Every image the parts data refers to, as paths inside geomorphs/."""
    paths = set()
    for file in sorted((data_dir / "geomorphs").glob("*.json")):
        for part in json.loads(file.read_text(encoding="utf-8")).values():
            for field in IMAGE_FIELDS:
                url = part.get(field)
                if url:
                    while url.startswith("../"):
                        url = url[3:]
                    paths.add(url.removeprefix("geomorphs/"))
    return paths


def download(name: str, destination: Path, base_urls=BASE_URLS) -> None:
    """Fetch a zip, trying each server in turn.

    This uses curl, not urllib, because Python from python.org on macOS can't
    verify certificates until its certificate installer has been run.
    """
    partial = destination.with_suffix(".part")
    problem = "no server tried"
    for base in base_urls:
        url = f"{base}/{name}"
        print(f"Downloading {url}")
        try:
            subprocess.run(
                ["curl", "--fail", "--location", "--progress-bar",
                 "--connect-timeout", "30", "--output", str(partial), url],
                check=True,
            )
            if zipfile.is_zipfile(partial):
                partial.replace(destination)
                return
            problem = "the download is not a valid zip file"
        except FileNotFoundError:
            raise OSError("This script needs curl, which was not found.") from None
        except subprocess.CalledProcessError as error:
            problem = f"curl stopped with code {error.returncode}"
        print(f"  Failed: {problem}")
        partial.unlink(missing_ok=True)
    raise OSError(f"Could not download {name}: {problem}")


def index_zips(zips: list[tuple[Path, str]]):
    """Find every file across the zips, by full path and by file name."""
    by_path: dict[str, tuple[Path, str]] = {}
    by_name: dict[str, list[tuple[Path, str]]] = {}
    for zip_path, folder in zips:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.namelist():
                if member.endswith("/"):
                    continue
                entry = (zip_path, member)
                by_path[f"{folder}/{member}"] = entry
                by_name.setdefault(Path(member).name, []).append(entry)
    return by_path, by_name


def find_source(relative_path, overrides, by_path, by_name):
    """A mapping in geomorph_mappings.json first, then a match on file name."""
    override = overrides.get(f"geomorphs/{relative_path}")
    if override and override in by_path:
        return by_path[override]
    matches = by_name.get(Path(relative_path).name)
    return matches[0] if matches else None


def populate(required, overrides, zips, target_dir: Path) -> tuple[int, list[str]]:
    by_path, by_name = index_zips(zips)
    copied = 0
    missing = []
    for relative_path in sorted(required):
        source = find_source(relative_path, overrides, by_path, by_name)
        if source is None:
            missing.append(relative_path)
            continue
        zip_path, member = source
        destination = target_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            with archive.open(member) as source_file, open(destination, "wb") as out:
                shutil.copyfileobj(source_file, out)
        copied += 1
    return copied, missing


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--high-res", action="store_true", help="use the larger images")
    parser.add_argument("--force", action="store_true", help="download the zips again")
    args = parser.parse_args(argv)

    mode = "highRes" if args.high_res else "lowRes"
    zips_dir = ZIPS_DIR / mode
    zips_dir.mkdir(parents=True, exist_ok=True)

    zips = []
    try:
        for name, folder in ZIPS[mode]:
            path = zips_dir / name
            if args.force or not (path.exists() and zipfile.is_zipfile(path)):
                download(name, path)
            else:
                print(f"Using the saved copy of {name}")
            zips.append((path, folder))
    except OSError as error:
        print(error, file=sys.stderr)
        return 1

    mappings = json.loads((DATA_DIR / "geomorph_mappings.json").read_text("utf-8"))
    required = required_images()
    print(f"Copying {len(required)} images into {TARGET_DIRS[mode]}")
    copied, missing = populate(
        required, mappings.get(mode, {}), zips, TARGET_DIRS[mode]
    )
    print(f"Copied {copied} of {len(required)} images.")
    for path in missing[:10]:
        print(f"  Missing: {path}")
    if len(missing) > 10:
        print(f"  ...and {len(missing) - 10} more")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
