"""Checks against REAL device files that you provide.

Put sample files in tests/real_samples/ (they are git-ignored), each with a
sidecar `<filename>.expected.json`, for example
    video-277_singular_display.MOV
    video-277_singular_display.MOV.expected.json -> {"device": "RAYBAN",
                                                    "capture_time": "2026-07-17 19:00:49"}
Without samples these tests are skipped.
"""
import json
from pathlib import Path

import pytest
from takeline.config import Settings
from takeline.pipeline import Analyzer, MediaItem

SAMPLES = Path(__file__).parent / "real_samples"
CASES = sorted(p for p in SAMPLES.glob("*.expected.json"))


@pytest.mark.parametrize("expected_file", CASES, ids=[c.name for c in CASES])
def test_real_sample(expected_file):
    media = expected_file.with_name(expected_file.name[: -len(".expected.json")])
    exp = json.loads(expected_file.read_text(encoding="utf-8"))
    s = Settings(use_exiftool=False, tz_mode=exp.get("tz_mode", "auto"), tz_zone=exp.get("tz_zone", "America/Sao_Paulo"))
    an = Analyzer(s)
    it = an.analyze(MediaItem(0, media))
    an.close()
    if "device" in exp:
        assert it.device == exp["device"], it.detection.reasons
    if "capture_time" in exp:
        assert it.capture_time.strftime("%Y-%m-%d %H:%M:%S") == exp["capture_time"], it.timestamp.to_dict()
    if "time_source" in exp:
        assert it.timestamp.source == exp["time_source"]


def test_samples_folder_documented():
    assert (SAMPLES / "README.md").exists()
