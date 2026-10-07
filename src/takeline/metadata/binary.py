"""Small, defensive binary-reading helpers.

Parsers read only headers and metadata boxes; they never load whole media
files. Every read is bounded so malformed sizes cannot exhaust memory.
"""

from __future__ import annotations

import datetime as _dt
import struct
from typing import BinaryIO

# Upper bound for any single metadata payload we are willing to load.
MAX_PAYLOAD = 32 * 1024 * 1024

QT_EPOCH = _dt.datetime(1904, 1, 1, tzinfo=_dt.timezone.utc)
UNIX_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
MKV_EPOCH = _dt.datetime(2001, 1, 1, tzinfo=_dt.timezone.utc)


class ParseError(Exception):
    """Raised for structurally invalid data that a parser cannot continue from."""


def read_exact(f: BinaryIO, n: int) -> bytes:
    if n < 0 or n > MAX_PAYLOAD:
        raise ParseError(f"refusing to read {n} bytes")
    data = f.read(n)
    if len(data) != n:
        raise ParseError("unexpected end of file")
    return data


def u8(b: bytes, o: int) -> int:
    return b[o]


def u16be(b: bytes, o: int) -> int:
    return struct.unpack_from(">H", b, o)[0]


def u32be(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


def u64be(b: bytes, o: int) -> int:
    return struct.unpack_from(">Q", b, o)[0]


def uint_be(b: bytes, o: int, size: int) -> int:
    if size == 0:
        return 0
    return int.from_bytes(b[o:o + size], "big")


def fourcc(b: bytes) -> str:
    """Decode a 4-byte type code; QuickTime uses MacRoman for the (c) sign."""
    try:
        return b.decode("mac_roman")
    except Exception:  # pragma: no cover - mac_roman decodes every byte
        return b.decode("latin-1")


def qt_seconds_to_utc(seconds: int) -> _dt.datetime | None:
    """QuickTime/MP4 header times are seconds since 1904-01-01 (UTC by spec).

    0 means "not set". Some muxers wrongly write Unix-epoch seconds; values that
    would land before 1970 are retried as Unix time.
    """
    if not seconds:
        return None
    try:
        dt = QT_EPOCH + _dt.timedelta(seconds=seconds)
        if dt.year < 1970:
            dt = UNIX_EPOCH + _dt.timedelta(seconds=seconds)
        return dt
    except (OverflowError, ValueError):
        return None


def cstring(b: bytes, o: int = 0, encoding: str = "utf-8") -> str:
    end = b.find(b"\x00", o)
    if end < 0:
        end = len(b)
    return b[o:end].decode(encoding, errors="replace")
