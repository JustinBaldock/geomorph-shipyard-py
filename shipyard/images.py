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

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap

from .custom import CUSTOM_FOLDER, IMAGE_FIELDS, LOW_RES_FOLDER_NAME, LOW_RES_SCALE

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
    folders.append(CUSTOM_FOLDER / LOW_RES_FOLDER_NAME)
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


def refresh_low_res(entries: dict, folder: Path) -> list[str]:
    """Make low-res copies that are missing, or older than their high-res image.

    Returns the images that changed, so anything cached can be dropped.
    """
    QImageReader.setAllocationLimit(IMAGE_MEMORY_LIMIT_MB)
    changed = []
    for entry in entries.values():
        for field in IMAGE_FIELDS:
            name = entry.get(field)
            high = Path(folder) / name if name else None
            if high is None or not high.is_file():
                continue
            low = Path(folder) / LOW_RES_FOLDER_NAME / name
            if low.is_file() and low.stat().st_mtime >= high.stat().st_mtime:
                continue
            image = QImage(str(high))
            if image.isNull():
                continue
            low.parent.mkdir(parents=True, exist_ok=True)
            smaller = image.scaled(
                image.size() * LOW_RES_SCALE,
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            )
            if smaller.save(str(low), "PNG"):
                changed.append(name)
    return changed


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

    def forget(self, relative_path: str) -> None:
        """Drop a cached image, after its file has been replaced."""
        self._cache.pop(relative_path, None)

    def has_any_part_images(self, library) -> bool:
        return any(self.find(info.url) for info in library.values())


class HighResStore:
    """High-res images for exports, falling back to the normal store.

    About 170 high-res tiles are a different shape from their low-res versions,
    so laying them on the part's space would stretch them. Those use the
    low-res image. Nothing is cached, as a ship's tiles wouldn't fit in memory.
    """

    def __init__(
        self,
        standard: ImageStore,
        folder: Path = HIGH_RES_FOLDER,
        custom_folder: Path = CUSTOM_FOLDER,
    ):
        self.standard = standard
        self.folder = folder
        self.custom_folder = custom_folder

    def available(self) -> bool:
        return self.folder.is_dir() and any(self.folder.iterdir())

    def find(self, relative_path: str | None) -> Path | None:
        """The high-res file for an image, if there is one."""
        if not relative_path:
            return None
        for folder in (self.folder, self.custom_folder):
            path = folder / relative_path
            if path.is_file():
                return path
        return None

    def pixmap(self, relative_path: str | None) -> QPixmap | None:
        if not relative_path:
            return None
        high = self.find(relative_path)
        normal = self.standard.find(relative_path)
        if high is not None and (normal is None or same_shape(high, normal)):
            pixmap = QPixmap(str(high))
            if not pixmap.isNull():
                return pixmap
        return self.standard.pixmap(relative_path)
