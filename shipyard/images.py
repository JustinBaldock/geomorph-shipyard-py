"""Finds the geomorph images on disk.

The images are not stored in this repository. This looks in, in order:

1. the folder named by the GEOMORPH_IMAGES environment variable
2. assets/geomorphs in this repository, where tools/fetch_geomorphs.py puts them
3. assets/geomorphs in a clone of the original project sitting next to this one

The app still works with no images at all, drawing labelled placeholders.

The high-res set in assets/geomorphs-highres is only used for exported images.
The low-res set is already sharper than the map can zoom, and one high-res tile
can take half a gigabyte of memory once it is loaded.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QImageReader, QPixmap

REPO_ROOT = Path(__file__).resolve().parent.parent
HIGH_RES_FOLDER = REPO_ROOT / "assets" / "geomorphs-highres"

# Qt refuses images over 256 MB by default, and the largest tile needs more.
IMAGE_MEMORY_LIMIT_MB = 1024
SAME_SHAPE_TOLERANCE = 0.03


def candidate_folders() -> list[Path]:
    folders = []
    override = os.environ.get("GEOMORPH_IMAGES")
    if override:
        folders.append(Path(override))
    folders.append(REPO_ROOT / "assets" / "geomorphs")
    folders.append(REPO_ROOT.parent / "geomorph-shipyard" / "assets" / "geomorphs")
    return folders


def same_shape(first: Path, second: Path) -> bool:
    """Compare proportions from the file headers, without loading the images."""
    size_a = QImageReader(str(first)).size()
    size_b = QImageReader(str(second)).size()
    if not (size_a.height() and size_b.height()):
        return False
    ratio_a = size_a.width() / size_a.height()
    ratio_b = size_b.width() / size_b.height()
    return abs(ratio_a - ratio_b) <= SAME_SHAPE_TOLERANCE


class ImageStore:
    def __init__(self, folders: list[Path] | None = None):
        QImageReader.setAllocationLimit(IMAGE_MEMORY_LIMIT_MB)
        self.folders = candidate_folders() if folders is None else folders
        self._cache: dict[str, QPixmap | None] = {}

    def find(self, relative_path: str) -> Path | None:
        for folder in self.folders:
            path = folder / relative_path
            if path.is_file():
                return path
        return None

    def pixmap(self, relative_path: str | None) -> QPixmap | None:
        """The image, or None if it isn't on disk or can't be read."""
        if not relative_path:
            return None
        if relative_path not in self._cache:
            path = self.find(relative_path)
            pixmap = QPixmap(str(path)) if path else None
            self._cache[relative_path] = (
                pixmap if pixmap is not None and not pixmap.isNull() else None
            )
        return self._cache[relative_path]

    def has_any_part_images(self, library) -> bool:
        return any(self.find(info.url) for info in library.values())


class HighResStore:
    """High-res images for exports, falling back to the normal store.

    About 170 high-res tiles are a different shape from their low-res versions,
    so laying them on the part's space would stretch them. Those use the
    low-res image. Nothing is cached, as a ship's tiles wouldn't fit in memory.
    """

    def __init__(self, standard: ImageStore, folder: Path = HIGH_RES_FOLDER):
        self.standard = standard
        self.folder = folder

    def available(self) -> bool:
        return self.folder.is_dir() and any(self.folder.iterdir())

    def pixmap(self, relative_path: str | None) -> QPixmap | None:
        if not relative_path:
            return None
        high = self.folder / relative_path
        normal = self.standard.find(relative_path)
        if high.is_file() and (normal is None or same_shape(high, normal)):
            pixmap = QPixmap(str(high))
            if not pixmap.isNull():
                return pixmap
        return self.standard.pixmap(relative_path)
