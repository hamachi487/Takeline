"""Timezone policy.

Three kinds of embedded timestamps exist:

* With an explicit offset (iPhone Keys:CreationDate "2026-07-17T19:15:30-0300",
  EXIF DateTimeOriginal + OffsetTimeOriginal). The wall clock where the media
  was captured is known exactly.
* UTC by specification (QuickTime movie/track/media headers, Matroska DateUTC,
  GPS time). Must be converted to a timezone to be meaningful.
* Without any timezone (EXIF without OffsetTime, some cameras' creation dates).
  This is the camera's own clock reading and is kept exactly as recorded.

Modes:
  auto    Keep recorded local times as recorded (offset or not). Convert
          UTC-only values to the system timezone. Never shifts a recorded time.
  system  Convert every value whose instant is known into the system timezone.
  manual  Same as system, using a chosen IANA zone (e.g. America/Sao_Paulo).
Timezone-less values are never shifted unless "treat timezone-less times as
UTC" is explicitly enabled.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from .parsing import ParsedDate

MODES = ("auto", "system", "manual")


@dataclass(frozen=True)
class Interpreted:
    local: dt.datetime            # naive wall-clock used for naming/sorting
    offset: dt.timedelta | None   # offset of that wall clock, when known
    explanation: str


def format_offset(off: dt.timedelta | None) -> str:
    if off is None:
        return "no timezone"
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "−"
    minutes = abs(minutes)
    return f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def list_zones() -> list[str]:
    try:
        return sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))
    except Exception:
        return []


@dataclass
class TimezonePolicy:
    mode: str = "auto"
    zone: str | None = None
    naive_is_utc: bool = False

    def __post_init__(self):
        if self.mode not in MODES:
            self.mode = "auto"
        self._zi = None
        if self.mode == "manual" and self.zone and ZoneInfo is not None:
            try:
                self._zi = ZoneInfo(self.zone)
            except (ZoneInfoNotFoundError, ValueError, OSError):
                self._zi = None

    # -------------------------------------------------------------- helpers
    @property
    def target_name(self) -> str:
        if self.mode == "manual" and self._zi is not None:
            return self.zone or "manual timezone"
        return "system timezone"

    def to_target(self, instant_utc: dt.datetime) -> dt.datetime:
        if instant_utc.tzinfo is None:
            instant_utc = instant_utc.replace(tzinfo=dt.timezone.utc)
        if self.mode == "manual" and self._zi is not None:
            return instant_utc.astimezone(self._zi)
        # astimezone() with no argument applies the OS rules for *that* instant,
        # so daylight-saving changes between capture and processing are handled.
        return instant_utc.astimezone()

    def describe(self) -> str:
        if self.mode == "auto":
            return "Automatic (keep recorded local time; UTC-only values shown in system timezone)"
        if self.mode == "system":
            return "System timezone"
        return f"Manual ({self.zone})" if self._zi else "Manual (invalid zone, using system timezone)"

    # -------------------------------------------------------------- interpret
    def _converted(self, instant: dt.datetime, why: str) -> Interpreted:
        local = self.to_target(instant)
        return Interpreted(local.replace(tzinfo=None), local.utcoffset(),
                           f"{why}; shown in {self.target_name} ({format_offset(local.utcoffset())})")

    def interpret(self, parsed: ParsedDate, semantics: str) -> Interpreted:
        """Turn a parsed value into the wall-clock time used for naming.

        semantics:
          'utc'   UTC by specification (QuickTime headers, Matroska DateUTC, GPS)
          'auto'  use the offset when the metadata states one, else a local clock reading
          'local' known to be the camera's local clock (never re-interpreted as UTC)
          'instant' an exact moment from the filesystem
        """
        if semantics == "utc":
            instant = parsed.instant() or parsed.wall.replace(tzinfo=dt.timezone.utc)
            return self._converted(instant, "Stored as UTC")
        if semantics == "instant":  # filesystem times: an exact moment
            instant = parsed.instant() or parsed.wall.replace(tzinfo=dt.timezone.utc)
            return self._converted(instant, "Filesystem time")
        if parsed.offset is not None:
            if self.mode == "auto":
                return Interpreted(parsed.wall, parsed.offset,
                                   f"Recorded local time ({format_offset(parsed.offset)} in metadata)")
            local = self.to_target(parsed.instant())
            if local.utcoffset() == parsed.offset:
                text = f"Recorded local time ({format_offset(parsed.offset)} in metadata)"
            else:
                text = (f"Recorded at {format_offset(parsed.offset)}; converted to "
                        f"{self.target_name} ({format_offset(local.utcoffset())})")
            return Interpreted(local.replace(tzinfo=None), local.utcoffset(), text)
        if semantics == "auto" and self.naive_is_utc:
            return self._converted(parsed.wall.replace(tzinfo=dt.timezone.utc),
                                   "No timezone in metadata; treated as UTC (setting)")
        return Interpreted(parsed.wall, None, "Recorded local time (no timezone in metadata; kept as recorded)")
