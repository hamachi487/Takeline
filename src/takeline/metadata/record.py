"""A grouped metadata store.

Every parser (native or ExifTool) writes into the same structure using
ExifTool-compatible "Group:Tag" names (Keys:CreationDate, ExifIFD:DateTimeOriginal,
UserData:Make, Track1:MediaCreateDate, XMP-xmp:CreateDate, ...). Detectors and
the timestamp resolver only ever talk to this structure, so adding a new
backend never requires touching detection logic.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

# Values longer than this are truncated when stored as text, to keep the
# inspector responsive and memory bounded for files with huge text blobs.
MAX_TEXT_LEN = 4000


@dataclass
class MetadataRecord:
    path: str
    container: str = "unknown"  # quicktime | heif | jpeg | png | tiff | riff | matroska | unknown
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    backends: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ write
    def set(self, group: str, tag: str, value: Any, overwrite: bool = False) -> None:
        if value is None:
            return
        if isinstance(value, str):
            value = value.replace("\x00", "").strip()
            if not value:
                return
            if len(value) > MAX_TEXT_LEN:
                value = value[:MAX_TEXT_LEN] + "…"
        elif isinstance(value, (bytes, bytearray)):
            value = describe_binary(bytes(value))
        bucket = self.groups.setdefault(group, {})
        if tag in bucket and not overwrite:
            if bucket[tag] == value:
                return
            # Keep every distinct value: add a numbered variant.
            n = 2
            while f"{tag} ({n})" in bucket:
                if bucket[f"{tag} ({n})"] == value:
                    return
                n += 1
            bucket[f"{tag} ({n})"] = value
            return
        bucket[tag] = value

    def error(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def merge(self, other: "MetadataRecord", prefer_existing: bool = True) -> None:
        for group, tags in other.groups.items():
            for tag, value in tags.items():
                if prefer_existing and tag in self.groups.get(group, {}):
                    continue
                self.groups.setdefault(group, {})[tag] = value
        for e in other.errors:
            self.error(e)
        for w in other.warnings:
            self.warn(w)
        for b in other.backends:
            if b not in self.backends:
                self.backends.append(b)

    # ------------------------------------------------------------------- read
    def get(self, qualified: str, default: Any = None) -> Any:
        group, _, tag = qualified.partition(":")
        return self.groups.get(group, {}).get(tag, default)

    def has(self, qualified: str) -> bool:
        return self.get(qualified) is not None

    def find(self, tag: str) -> list[tuple[str, Any]]:
        """All (group, value) pairs for a tag name, across groups."""
        out = []
        for group, tags in self.groups.items():
            if tag in tags:
                out.append((group, tags[tag]))
        return out

    def match(self, pattern: str) -> list[tuple[str, Any]]:
        """Glob-ish lookup: 'Track*:MediaCreateDate', '*:Make', 'XMP-*:CreateDate'."""
        gpat, _, tpat = pattern.partition(":")
        grx = re.compile("^" + re.escape(gpat).replace(r"\*", ".*") + "$")
        trx = re.compile("^" + re.escape(tpat).replace(r"\*", ".*") + "$")
        out = []
        for group, tags in self.groups.items():
            if not grx.match(group):
                continue
            for tag, value in tags.items():
                if trx.match(tag):
                    out.append((f"{group}:{tag}", value))
        return out

    def items(self) -> Iterator[tuple[str, str, Any]]:
        for group, tags in self.groups.items():
            for tag, value in tags.items():
                yield group, tag, value

    def strings(self) -> Iterator[tuple[str, str]]:
        """Every textual value as (qualified_name, text)."""
        for group, tag, value in self.items():
            if isinstance(value, str):
                yield f"{group}:{tag}", value

    def flat(self) -> dict[str, Any]:
        return {f"{g}:{t}": v for g, t, v in self.items()}

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "container": self.container,
            "backends": list(self.backends),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "groups": {g: {t: jsonable(v) for t, v in tags.items()} for g, tags in self.groups.items()},
        }


def jsonable(value: Any) -> Any:
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def describe_binary(data: bytes) -> str:
    """Render a binary blob as text if it is clearly text, else a short summary."""
    text = printable_text(data)
    if text is not None:
        return text
    return f"<binary data, {len(data)} bytes>"


def printable_text(data: bytes, min_ratio: float = 0.92) -> str | None:
    if not data:
        return None
    sample = data[:2048].rstrip(b"\x00")
    if not sample:
        return None
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        text = sample.decode("latin-1")
    good = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    if good / max(1, len(text)) >= min_ratio:
        if len(data) > 2048:
            text += "…"
        return text.strip()
    return None
