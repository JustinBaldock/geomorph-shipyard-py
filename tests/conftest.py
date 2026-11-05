"""Keeps tests away from the real custom parts folder.

Loading custom parts can write files, so the app's default folder is pointed
somewhere temporary for every test.
"""

import pytest

from shipyard import custom


@pytest.fixture(autouse=True)
def separate_custom_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(custom, "CUSTOM_FOLDER", tmp_path / "default-custom")
