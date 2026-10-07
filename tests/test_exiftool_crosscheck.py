"""Keeps the synthetic builders honest: ExifTool must read them the same way.

Skipped when ExifTool is not installed.
"""
import datetime as dt
import json
import shutil
import subprocess

import builders as b
import pytest
from takeline.metadata import MetadataExtractor
from conftest import UTC, iphone_movie, rayban_movie

pytestmark = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")


def et(path):
    out = subprocess.run(["exiftool", "-j", "-G1", "-a", "-s", str(path)], capture_output=True, check=True)
    return json.loads(out.stdout)[0]


def test_builders_agree_with_exiftool(media):
    files = [
        rayban_movie(media / "r.MOV", "2026-07-17T19:00:49", dt.datetime(2026, 7, 17, 22, 0, 49, tzinfo=UTC)),
        iphone_movie(media / "i.MOV", "2026-07-17T19:15:30-0300", None),
        b.build_heic(media / "h.HEIC", b.tiff_bytes(*b.exif_dict("Apple", "iPhone 15", None, "2026:07:17 19:20:01"))),
        b.build_jpeg(media / "j.jpg", b.tiff_bytes(*b.exif_dict("Canon", "Canon EOS R6", None, "2026:07:17 19:44:22"))),
    ]
    x = MetadataExtractor(use_exiftool=False)
    for f in files:
        mine, theirs = x.extract(f), et(f)
        for key in ("Keys:Make", "Keys:Model", "IFD0:Make", "IFD0:Model"):
            if key in theirs:
                assert str(mine.get(key)) == str(theirs[key]), (f.name, key)
        for key in ("Keys:CreationDate", "ExifIFD:DateTimeOriginal"):
            if key in theirs:
                digits = lambda s: "".join(ch for ch in str(s) if ch.isdigit())[:14]  # noqa: E731
                assert digits(mine.get(key)) == digits(theirs[key]), (f.name, key)


def test_exiftool_backend_merges(media):
    p = iphone_movie(media / "i.MOV", "2026-07-17T19:15:30-0300", None)
    x = MetadataExtractor(use_exiftool=True)
    try:
        r = x.extract(p)
    finally:
        x.close()
    assert "exiftool" in r.backends and r.get("Keys:Make") == "Apple"
