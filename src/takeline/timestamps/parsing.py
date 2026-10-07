"""Parsing of date/time values found in metadata and filenames.

A ParsedDate keeps exactly what the file says: the wall-clock reading and,
only if the metadata states one, its UTC offset. No timezone is assumed here;
interpretation happens in timezone.TimezonePolicy.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

MIN_YEAR = 1990


@dataclass(frozen=True)
class ParsedDate:
    wall: dt.datetime                 # naive wall-clock reading as written
    offset: dt.timedelta | None       # explicit UTC offset, if the metadata has one
    date_only: bool = False

    @property
    def has_offset(self) -> bool:
        return self.offset is not None

    def instant(self) -> dt.datetime | None:
        """UTC instant, only when the offset is known."""
        if self.offset is None:
            return None
        return (self.wall - self.offset).replace(tzinfo=dt.timezone.utc)


_DATE_RX = re.compile(
    r"^\s*(?P<y>\d{4})[:\-/.](?P<mo>\d{1,2})[:\-/.](?P<d>\d{1,2})"
    r"(?:[ T_]+(?P<h>\d{1,2})[:.]?(?P<mi>\d{2})(?:[:.]?(?P<s>\d{2}))?(?:[.,](?P<frac>\d+))?)?"
    r"\s*(?P<tz>Z|UTC|GMT|[+\-]\d{2}(?::?\d{2})?)?(?:\s*DST)?\s*$",
    re.IGNORECASE,
)
_OFFSET_RX = re.compile(r"^\s*([+\-])(\d{2}):?(\d{2})?\s*$")


def parse_offset(text) -> dt.timedelta | None:
    if not isinstance(text, str):
        return None
    t = text.strip()
    if t.upper() in ("Z", "UTC", "GMT", "+00:00"):
        return dt.timedelta(0)
    m = _OFFSET_RX.match(t)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    hours, minutes = int(m.group(2)), int(m.group(3) or 0)
    if hours > 14 or minutes >= 60:
        return None
    return sign * dt.timedelta(hours=hours, minutes=minutes)


def parse_date_value(value) -> ParsedDate | None:
    """Parse a metadata value (datetime object or one of many string formats)."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            off = value.utcoffset()
            return _validated(ParsedDate(value.replace(tzinfo=None), off))
        return _validated(ParsedDate(value, None))
    if not isinstance(value, str):
        return None
    text = value.strip().strip("\x00")
    if not text or set(text) <= set("0: -T"):
        return None
    m = _DATE_RX.match(text)
    if m:
        try:
            y, mo, d = int(m["y"]), int(m["mo"]), int(m["d"])
            if m["h"] is None:
                return _validated(ParsedDate(dt.datetime(y, mo, d), None, date_only=True))
            h, mi, s = int(m["h"]), int(m["mi"]), int(m["s"] or 0)
            micro = int((m["frac"] or "0")[:6].ljust(6, "0"))
            wall = dt.datetime(y, mo, d, h, mi, s, micro)
        except ValueError:
            return None
        off = parse_offset(m["tz"]) if m["tz"] else None
        return _validated(ParsedDate(wall, off))
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %b %d %Y %H:%M:%S"):  # AVI IDIT styles
        try:
            return _validated(ParsedDate(dt.datetime.strptime(" ".join(text.split()), fmt), None))
        except ValueError:
            continue
    return None


def parse_iptc(date_value, time_value) -> ParsedDate | None:
    """IPTC DateCreated 'YYYYMMDD' (or YYYY:MM:DD) + TimeCreated 'HHMMSS+HHMM'."""
    if not isinstance(date_value, str):
        return None
    ds = re.sub(r"\D", "", date_value)
    if len(ds) != 8:
        return None
    ts = time_value if isinstance(time_value, str) else ""
    m = re.match(r"^\s*(\d{2}):?(\d{2}):?(\d{2})\s*([+\-]\d{2}:?\d{2})?", ts)
    try:
        if m:
            wall = dt.datetime(int(ds[:4]), int(ds[4:6]), int(ds[6:]), int(m[1]), int(m[2]), int(m[3]))
            return _validated(ParsedDate(wall, parse_offset(m[4]) if m[4] else None))
        return _validated(ParsedDate(dt.datetime(int(ds[:4]), int(ds[4:6]), int(ds[6:])), None, True))
    except ValueError:
        return None


def _validated(p: ParsedDate) -> ParsedDate | None:
    now = dt.datetime.now()
    if p.wall.year < MIN_YEAR or p.wall > now + dt.timedelta(days=2):
        return None
    # Common "clock never set" / zero placeholders
    if (p.wall.month, p.wall.day, p.wall.hour, p.wall.minute, p.wall.second) == (1, 1, 0, 0, 0) and \
            p.wall.year in (1970, 1980, 2000, 2001):
        return None
    return p


# --------------------------------------------------------------- filenames
_FILENAME_PATTERNS = [
    # IMG_20260717_190049, PXL_20260717_190049123, VID_20260717_190049, DJI_20260717190049_0001
    re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})(?P<mo>\d{2})(?P<d>\d{2})[_\-T ]?(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})(?:\d{3})?(?!\d)"),
    # RAYBAN-2026-07-17-190049, 2026-07-17 19.00.49, Screen Recording 2026-07-17 at 19.00.49
    re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})-(?P<mo>\d{2})-(?P<d>\d{2})(?:[ _\-T]|\s+at\s+)(?P<h>\d{2})[.\-:h]?(?P<mi>\d{2})[.\-:m]?(?P<s>\d{2})(?!\d)", re.IGNORECASE),
]


def parse_filename_date(name: str) -> ParsedDate | None:
    stem = name.rsplit(".", 1)[0]
    for rx in _FILENAME_PATTERNS:
        m = rx.search(stem)
        if not m:
            continue
        try:
            wall = dt.datetime(int(m["y"]), int(m["mo"]), int(m["d"]),
                               int(m["h"]), int(m["mi"]), int(m["s"]))
        except ValueError:
            continue
        p = _validated(ParsedDate(wall, None))
        if p:
            return p
    return None
