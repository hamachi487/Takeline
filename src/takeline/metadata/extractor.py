"""Metadata extraction entry point.

Format is chosen by sniffing file content (not the extension), so a HEIC saved
as .jpg or an MP4 renamed to .mov is still read correctly.
"""

from __future__ import annotations

import datetime as _dt
import os
import struct
from pathlib import Path

from .binary import ParseError
from .containers import PNG_SIG, parse_matroska, parse_png, parse_riff, parse_tiff_file
from .exif import parse_jpeg
from .exiftool_backend import ExifToolSession, exiftool_record, locate_exiftool
from .quicktime import IsoBmffParser, looks_like_isobmff
from .record import MetadataRecord

VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff"}
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS

EXPECTED_CONTAINER = {
    ".mov": "quicktime", ".mp4": "quicktime", ".m4v": "quicktime", ".avi": "riff",
    ".mkv": "matroska", ".webm": "matroska", ".jpg": "jpeg", ".jpeg": "jpeg",
    ".heic": "heif", ".heif": "heif", ".png": "png", ".tif": "tiff", ".tiff": "tiff",
}


def sniff(head: bytes) -> str:
    if looks_like_isobmff(head):
        return "isobmff"
    if head[:3] == b"\xFF\xD8\xFF":
        return "jpeg"
    if head[:8] == PNG_SIG:
        return "png"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:4] == b"RIFF":
        return "riff"
    if head[:4] == b"\x1A\x45\xDF\xA3":
        return "matroska"
    return "unknown"


class MetadataExtractor:
    def __init__(self, use_exiftool: bool = True, exiftool_path: str | None = None):
        self.exiftool_exe = locate_exiftool(exiftool_path) if use_exiftool else None
        self._session: ExifToolSession | None = None

    @property
    def exiftool_available(self) -> bool:
        return self.exiftool_exe is not None

    def close(self) -> None:
        if self._session:
            self._session.close()
            self._session = None

    def extract(self, path: str | os.PathLike) -> MetadataRecord:
        path = str(path)
        record = MetadataRecord(path=path)
        p = Path(path)
        try:
            st = p.stat()
        except OSError as exc:
            record.error(f"Could not access file: {exc.strerror or exc}")
            return record
        record.set("File", "FileName", p.name)
        record.set("File", "Directory", str(p.parent))
        record.set("File", "FileSize", st.st_size)
        record.set("File", "FileExtension", p.suffix.lower().lstrip("."))
        record.set("System", "FileModifyDate", _fs_time(st.st_mtime))
        birth = getattr(st, "st_birthtime", None)
        if birth is None and os.name == "nt":
            birth = st.st_ctime
        if birth:
            record.set("System", "FileCreateDate", _fs_time(birth))
        if st.st_size == 0:
            record.error("File is empty")
            return record

        try:
            with open(path, "rb") as f:
                head = f.read(16)
                kind = sniff(head)
                record.backends.append("native")
                if kind == "isobmff":
                    IsoBmffParser(f, record, st.st_size).parse()
                elif kind == "jpeg":
                    parse_jpeg(f, record)
                elif kind == "png":
                    parse_png(f, record)
                elif kind == "tiff":
                    parse_tiff_file(f, record, st.st_size)
                elif kind == "riff":
                    parse_riff(f, record, st.st_size)
                elif kind == "matroska":
                    parse_matroska(f, record, st.st_size)
                else:
                    record.error("Unrecognized or unsupported file content")
        except PermissionError:
            record.error("Could not read metadata: permission denied or the file is locked by another program")
        except (ParseError, struct.error, ValueError, IndexError, EOFError) as exc:
            record.error(f"Could not read metadata: {exc}")
        except OSError as exc:
            record.error(f"Could not read metadata: {exc.strerror or exc}")
        except Exception as exc:  # never let one file kill a batch
            record.error(f"Could not read metadata: unexpected {type(exc).__name__}: {exc}")

        expected = EXPECTED_CONTAINER.get(p.suffix.lower())
        if expected and record.container not in (expected, "unknown") and not record.errors:
            record.warn(f"File content is {record.container.upper()} although the extension is {p.suffix}")

        if self.exiftool_exe:
            self._merge_exiftool(record)
        return record

    def _merge_exiftool(self, record: MetadataRecord) -> None:
        if not self.exiftool_exe:
            return
        try:
            if self._session is None:
                self._session = ExifToolSession(self.exiftool_exe)
            et = exiftool_record(self._session, record.path)
        except Exception as exc:  # ExifTool problems must never break a scan
            record.warn(f"ExifTool unavailable: {exc}")
            return
        if et is None:
            return
        had_errors = bool(record.errors)
        record.merge(et, prefer_existing=True)
        if had_errors and len(et.groups) > 1:
            # ExifTool could read what the native parser could not: downgrade to a warning.
            record.warnings.extend(f"Native parser: {e}" for e in record.errors)
            record.errors.clear()


def _fs_time(ts: float) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).astimezone()
