import datetime as dt
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    """Never touch the developer's real settings or undo history."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    yield


@pytest.fixture
def media(tmp_path):
    d = tmp_path / "media"
    d.mkdir()
    return d


def rayban_movie(path, local: str, utc: dt.datetime | None, model="Ray-Ban Meta Smart Glasses", make="Meta AI"):
    """SYNTHETIC Ray-Ban-style clip: identifiers per the product brief, not a real capture."""
    import builders as b
    keys = {}
    if make:
        keys["com.apple.quicktime.make"] = make
    if model:
        keys["com.apple.quicktime.model"] = model
    if local:
        keys["com.apple.quicktime.creationdate"] = local
    return b.build_movie(path, mvhd_time=utc, keys=keys, video_handler="VideoHandler", pascal=False, codec=b"avc1")


def iphone_movie(path, local: str, utc: dt.datetime | None, model="iPhone 15 Pro"):
    """SYNTHETIC iPhone-style clip (Apple key layout with offset-bearing creation date)."""
    import builders as b
    return b.build_movie(path, mvhd_time=utc, keys={
        "com.apple.quicktime.location.accuracy.horizontal": "4.7",
        "com.apple.quicktime.location.ISO6709": "-23.5505-046.6333+760.000/",
        "com.apple.quicktime.make": "Apple", "com.apple.quicktime.model": model,
        "com.apple.quicktime.software": "17.5.1", "com.apple.quicktime.creationdate": local})
