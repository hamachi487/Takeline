"""User settings, stored as JSON in the per-user application-data folder.

Windows: %APPDATA%\\Takeline\\settings.json  (migrated from %APPDATA%\\CaptureSort)
Other:   ~/.config/takeline/settings.json
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .naming.template import DEFAULT_SUFFIX, DEFAULT_TEMPLATE


LEGACY_NAMES = ("CaptureSort", "capturesort")  # folder used before the app was renamed


def _app_data_base() -> tuple[Path, str]:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"), "Takeline"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support", "Takeline"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"), "takeline"


def app_data_dir() -> Path:
    """Per-user folder for settings and undo history.

    Earlier versions were called CaptureSort; their folder is moved here once,
    so settings and the undo history survive the rename.
    """
    base, name = _app_data_base()
    d = base / name
    if not d.exists():
        for legacy_name in LEGACY_NAMES:
            legacy = base / legacy_name
            if legacy.is_dir() and legacy.name != d.name:
                try:
                    os.rename(legacy, d)
                except OSError:
                    return legacy  # could not move (e.g. file in use): keep using the old folder
                break
    return d


def history_dir() -> Path:
    return app_data_dir() / "history"


@dataclass
class Settings:
    # Timezone
    tz_mode: str = "auto"                 # auto | system | manual
    tz_zone: str = "America/Sao_Paulo"    # used when tz_mode == manual
    naive_is_utc: bool = False            # treat timezone-less capture dates as UTC
    # Naming
    template: str = DEFAULT_TEMPLATE
    suffix_format: str = DEFAULT_SUFFIX
    ext_case: str = "preserve"            # preserve | lower | upper
    # Scanning / detection
    include_subfolders: bool = True
    unknown_threshold: float = 0.60
    allow_filename_dates: bool = True
    allow_filesystem_fallback: bool = True
    # Metadata engines
    use_exiftool: bool = True             # only if a bundled/installed exiftool is found
    exiftool_path: str = ""
    # UI
    developer_mode: bool = False
    last_copy_destination: str = ""
    # Advanced: per-device timestamp rules (merged over built-in defaults)
    device_profiles: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def path(cls) -> Path:
        return app_data_dir() / "settings.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        p = path or cls.path()
        s = cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        known = {f.name for f in fields(cls)}
        for k, v in data.items():
            if k in known and not k.startswith("_"):
                setattr(s, k, v)
        if s.tz_mode not in ("auto", "system", "manual"):
            s.tz_mode = "auto"
        return s

    def save(self, path: Path | None = None) -> None:
        p = path or self.path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            pass  # settings are a convenience; never fail an operation over them
