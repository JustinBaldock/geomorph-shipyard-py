"""Checks the image script against fake zips, with no network use."""

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import fetch_geomorphs as fetch  # noqa: E402


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def test_required_images_covers_every_kind_of_image(tmp_path):
    folder = tmp_path / "geomorphs"
    folder.mkdir()
    (folder / "X.json").write_text(
        json.dumps(
            {
                "X-1": {
                    "url": "X/a.png",
                    "mirrorUrl": "../geomorphs/X/b.png",
                    "overlayUrl": "../../X/c.png",
                    "overlayMirrorUrl": "geomorphs/X/d.png",
                }
            }
        )
    )
    assert fetch.required_images(tmp_path) == {
        "X/a.png",
        "X/b.png",
        "X/c.png",
        "X/d.png",
    }


def test_real_data_asks_for_every_image():
    assert len(fetch.required_images()) > 3000


def test_mapping_wins_over_a_file_name_match(tmp_path):
    zip_path = make_zip(
        tmp_path / "a.zip",
        {"Deep/x.png": b"by name", "Other/renamed.png": b"mapped"},
    )
    target = tmp_path / "out"
    copied, missing = fetch.populate(
        {"LS/x.png"},
        {"geomorphs/LS/x.png": "AdventureClass/Other/renamed.png"},
        [(zip_path, "AdventureClass")],
        target,
    )
    assert (copied, missing) == (1, [])
    assert (target / "LS/x.png").read_bytes() == b"mapped"


def test_falls_back_to_the_file_name(tmp_path):
    zip_path = make_zip(tmp_path / "a.zip", {"Deep/Folder/x.png": b"data"})
    target = tmp_path / "out"
    fetch.populate({"LS/x.png"}, {}, [(zip_path, "Geomorphs")], target)
    assert (target / "LS/x.png").read_bytes() == b"data"


def test_reports_missing_images(tmp_path):
    zip_path = make_zip(tmp_path / "a.zip", {"other.png": b"data"})
    copied, missing = fetch.populate(
        {"LS/x.png"}, {}, [(zip_path, "Geomorphs")], tmp_path / "out"
    )
    assert (copied, missing) == (0, ["LS/x.png"])


def test_two_zips_can_share_a_folder(tmp_path):
    first = make_zip(tmp_path / "1.zip", {"a.png": b"one"})
    second = make_zip(tmp_path / "2.zip", {"b.png": b"two"})
    target = tmp_path / "out"
    copied, _ = fetch.populate(
        {"A/a.png", "B/b.png"},
        {},
        [(first, "AdventureClass"), (second, "AdventureClass")],
        target,
    )
    assert copied == 2


def test_a_hostile_zip_path_stays_inside_the_target(tmp_path):
    zip_path = make_zip(tmp_path / "a.zip", {"../../evil.png": b"data"})
    target = tmp_path / "deep" / "out"
    fetch.populate({"LS/evil.png"}, {}, [(zip_path, "Geomorphs")], target)
    assert (target / "LS/evil.png").exists()
    assert not (tmp_path / "evil.png").exists()


class FakeCurl:
    """Stands in for curl, failing for any server listed in `broken`."""

    def __init__(self, tmp_path, broken=(), bad_zip=False):
        self.broken = broken
        self.bad_zip = bad_zip
        self.good = make_zip(tmp_path / "good.zip", {"a.png": b"data"})
        self.urls = []

    def __call__(self, command, check):
        output, url = command[command.index("--output") + 1], command[-1]
        self.urls.append(url)
        assert "--insecure" not in command and "-k" not in command
        if any(server in url for server in self.broken):
            raise fetch.subprocess.CalledProcessError(22, command)
        data = b"<html>not a zip</html>" if self.bad_zip else self.good.read_bytes()
        Path(output).write_bytes(data)


def test_download_saves_a_good_zip(tmp_path, monkeypatch):
    curl = FakeCurl(tmp_path)
    monkeypatch.setattr(fetch.subprocess, "run", curl)
    destination = tmp_path / "Geomorphs.zip"
    fetch.download("Geomorphs.zip", destination, ("https://one", "https://two"))
    assert zipfile.is_zipfile(destination)
    assert curl.urls == ["https://one/Geomorphs.zip"]


def test_download_falls_back_to_the_mirror(tmp_path, monkeypatch):
    curl = FakeCurl(tmp_path, broken=("https://one",))
    monkeypatch.setattr(fetch.subprocess, "run", curl)
    destination = tmp_path / "Geomorphs.zip"
    fetch.download("Geomorphs.zip", destination, ("https://one", "https://two"))
    assert curl.urls == ["https://one/Geomorphs.zip", "https://two/Geomorphs.zip"]
    assert destination.exists()


def test_download_rejects_a_page_that_is_not_a_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.subprocess, "run", FakeCurl(tmp_path, bad_zip=True))
    destination = tmp_path / "Geomorphs.zip"
    with pytest.raises(OSError, match="not a valid zip"):
        fetch.download("Geomorphs.zip", destination, ("https://one",))
    assert not destination.exists()
    assert not destination.with_suffix(".part").exists()
