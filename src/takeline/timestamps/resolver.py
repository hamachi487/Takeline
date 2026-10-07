"""Capture timestamp resolver.

Collects every date in the metadata, ranks them, and explains the choice.

Tiers (lower wins; within a tier, higher confidence wins):
  1  Device-specific capture timestamp (from the device profile, e.g.
     Keys:CreationDate for Ray-Ban Meta and iPhone video)
  2  Original-capture fields (EXIF DateTimeOriginal, QuickTime Keys
     CreationDate, AVI IDIT, XMP exif:DateTimeOriginal)
  3  Manufacturer-specific capture fields (e.g. Sony XML CreationDate)
  4  Other embedded creation dates (QuickTime movie/track/media headers,
     XMP CreateDate, IPTC, ©day, Matroska DateUTC, PNG, GPS)
  5  Date written in the filename by a camera, or EXIF ModifyDate
  6  Filesystem modified time (last resort, flagged in the UI)

Device detection and timestamp resolution are separate; the resolver only
receives the detected device code to look up its profile.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from ..metadata.record import MetadataRecord
from .parsing import ParsedDate, parse_date_value, parse_filename_date, parse_iptc, parse_offset
from .timezone import Interpreted, TimezonePolicy, format_offset


@dataclass(frozen=True)
class Spec:
    pattern: str
    tier: int
    confidence: float
    semantics: str  # utc | auto | local | exif
    label: str


SPECS: list[Spec] = [
    Spec("Keys:CreationDate", 2, 0.97, "auto", "QuickTime Keys creation date"),
    Spec("ExifIFD:DateTimeOriginal", 2, 0.97, "exif", "EXIF original capture time"),
    Spec("XMP-exif:DateTimeOriginal", 2, 0.92, "auto", "XMP EXIF original capture time"),
    Spec("UserData:DateTimeOriginal", 2, 0.90, "auto", "QuickTime user-data capture date"),
    Spec("RIFF:DateTimeOriginal", 2, 0.88, "local", "AVI capture date (IDIT)"),
    Spec("XML:CreationDateValue", 3, 0.95, "auto", "Camera XML creation date"),
    Spec("ExifIFD:CreateDate", 4, 0.88, "exif", "EXIF digitized time"),
    Spec("XMP-photoshop:DateCreated", 4, 0.85, "auto", "XMP Photoshop date created"),
    Spec("XMP-xmp:CreateDate", 4, 0.85, "auto", "XMP creation date"),
    Spec("UserData:ContentCreateDate", 4, 0.85, "auto", "QuickTime content creation date (©day)"),
    Spec("ItemList:ContentCreateDate", 4, 0.82, "auto", "QuickTime item-list creation date (©day)"),
    Spec("Keys:ContentCreateDate", 4, 0.82, "auto", "QuickTime Keys content creation date"),
    Spec("QuickTime:CreateDate", 4, 0.80, "utc", "QuickTime movie header creation time"),
    Spec("Track*:MediaCreateDate", 4, 0.75, "utc", "QuickTime media header creation time"),
    Spec("Track*:TrackCreateDate", 4, 0.72, "utc", "QuickTime track header creation time"),
    Spec("Matroska:DateTimeOriginal", 4, 0.80, "utc", "Matroska date (UTC)"),
    Spec("Keys:creation_time", 4, 0.70, "auto", "QuickTime creation_time key"),
    Spec("PNG:CreationTime", 4, 0.70, "auto", "PNG creation time"),
    Spec("RIFF:DateCreated", 4, 0.65, "local", "AVI INFO creation date"),
    Spec("IFD0:ModifyDate", 5, 0.45, "exif", "EXIF modify date (may reflect editing)"),
]
HEADER_PATTERNS = ("QuickTime:CreateDate", "Track*:MediaCreateDate", "Track*:TrackCreateDate")
EXIF_OFFSET_FOR = {"DateTimeOriginal": "OffsetTimeOriginal", "CreateDate": "OffsetTimeDigitized",
                   "ModifyDate": "OffsetTime"}

DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    # preferred: tags promoted to tier 1 for this device.
    # quicktime_header_local: the camera writes LOCAL time into QuickTime
    # movie/track headers (which the spec defines as UTC).
    "RAYBAN": {"preferred": ["Keys:CreationDate", "ExifIFD:DateTimeOriginal", "XMP-exif:DateTimeOriginal"],
               "quicktime_header_local": False},
    "OAKLEY": {"preferred": ["Keys:CreationDate", "ExifIFD:DateTimeOriginal", "XMP-exif:DateTimeOriginal"],
               "quicktime_header_local": False},
    "APPLE": {"preferred": ["Keys:CreationDate", "ExifIFD:DateTimeOriginal"],
              "quicktime_header_local": False},
    "SONY": {"preferred": ["XML:CreationDateValue", "ExifIFD:DateTimeOriginal"],
             "quicktime_header_local": False},
    "CANON": {"preferred": ["ExifIFD:DateTimeOriginal"], "quicktime_header_local": True},
    "GOPRO": {"preferred": ["ExifIFD:DateTimeOriginal"], "quicktime_header_local": True},
    "DJI": {"preferred": ["ExifIFD:DateTimeOriginal", "XMP-exif:DateTimeOriginal"],
            "quicktime_header_local": False},
}


@dataclass
class Candidate:
    source: str
    label: str
    raw: Any
    parsed: ParsedDate
    tier: int
    confidence: float
    semantics: str
    interp: Interpreted | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source, "label": self.label, "raw": str(self.raw), "tier": self.tier,
            "confidence": round(self.confidence, 3), "semantics": self.semantics,
            "interpreted": self.interp.local.isoformat(sep=" ") if self.interp else None,
            "offset": format_offset(self.interp.offset) if self.interp else None,
            "explanation": self.interp.explanation if self.interp else None,
        }


@dataclass
class TimestampResult:
    local: dt.datetime
    offset: dt.timedelta | None
    source: str
    label: str
    confidence: float
    interpretation: str
    notes: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    is_fallback: bool = False
    from_filename: bool = False

    def display(self) -> str:
        return self.local.strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict:
        return {
            "timestamp": self.local.isoformat(sep=" ", timespec="seconds"),
            "offset": format_offset(self.offset), "source": self.source, "label": self.label,
            "confidence": round(self.confidence, 3), "interpretation": self.interpretation,
            "notes": list(self.notes), "is_fallback": self.is_fallback,
            "candidates": [c.to_dict() for c in self.candidates],
        }


class CaptureTimestampResolver:
    def __init__(self, policy: TimezonePolicy | None = None,
                 profiles: dict[str, dict[str, Any]] | None = None,
                 allow_filename: bool = True, allow_filesystem: bool = True):
        self.policy = policy or TimezonePolicy()
        self.profiles = {**DEFAULT_PROFILES, **(profiles or {})}
        self.allow_filename = allow_filename
        self.allow_filesystem = allow_filesystem

    # ------------------------------------------------------------- collect
    def candidates(self, record: MetadataRecord, device: str | None = None) -> list[Candidate]:
        profile = self.profiles.get(device or "", {})
        preferred = list(profile.get("preferred", []))
        header_local = bool(profile.get("quicktime_header_local", False))
        out: list[Candidate] = []
        seen: set[tuple[str, str]] = set()

        for spec in SPECS:
            for qname, raw in record.match(spec.pattern):
                parsed = self._parse(record, qname, raw, spec.semantics)
                if parsed is None:
                    continue
                key = (qname, parsed.wall.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                tier, conf, sem, label = spec.tier, spec.confidence, spec.semantics, spec.label
                if sem == "exif":
                    sem = "auto"
                base_q = qname.split(" (")[0]
                if header_local and any(_glob(p, base_q) for p in HEADER_PATTERNS):
                    # The header claims UTC, but this camera writes its local clock.
                    sem = "local"
                    parsed = ParsedDate(parsed.wall, None, parsed.date_only)
                    label += " (this device writes local time here)"
                    conf = min(0.9, conf + 0.05)
                if base_q in preferred:
                    tier = 1
                    conf = max(conf, 0.97 - 0.01 * preferred.index(base_q))
                out.append(Candidate(qname, label, raw, parsed, tier, conf, sem))

        gps = self._gps(record)
        if gps:
            out.append(gps)
        iptc = parse_iptc(record.get("IPTC:DateCreated"), record.get("IPTC:TimeCreated"))
        if iptc:
            out.append(Candidate("IPTC:DateCreated", "IPTC date created",
                                 f"{record.get('IPTC:DateCreated')} {record.get('IPTC:TimeCreated') or ''}".strip(),
                                 iptc, 4, 0.78 if not iptc.date_only else 0.3, "auto"))
        if self.allow_filename:
            name = record.get("File:FileName") or ""
            fp = parse_filename_date(name)
            if fp:
                out.append(Candidate("Filename", "Date/time written in the filename", name, fp, 5, 0.55, "local"))
        if self.allow_filesystem:
            fm = record.get("System:FileModifyDate")
            if isinstance(fm, dt.datetime):
                p = parse_date_value(fm)
                if p:
                    out.append(Candidate("System:FileModifyDate",
                                         "File modified time (filesystem fallback; may reflect copying)",
                                         fm.isoformat(sep=" ", timespec="seconds"), p, 6, 0.15, "instant"))
        for c in out:
            c.interp = self.policy.interpret(c.parsed, c.semantics)
            if c.parsed.date_only:
                c.confidence = min(c.confidence, 0.35)
        out.sort(key=lambda c: (c.tier, -c.confidence))
        return out

    def _parse(self, record: MetadataRecord, qname: str, raw, semantics: str) -> ParsedDate | None:
        parsed = parse_date_value(raw)
        if parsed is None:
            return None
        if semantics == "exif" and parsed.offset is None:
            group, tag = qname.split(":", 1)
            off_tag = EXIF_OFFSET_FOR.get(tag.split(" (")[0])
            if off_tag:
                off = parse_offset(record.get(f"{group}:{off_tag}"))
                if off is None and tag.startswith("DateTimeOriginal"):
                    off = parse_offset(record.get(f"{group}:OffsetTime"))
                if off is not None:
                    parsed = ParsedDate(parsed.wall, off, parsed.date_only)
        if semantics == "utc" and isinstance(raw, str) and parsed.offset is None:
            parsed = ParsedDate(parsed.wall, dt.timedelta(0), parsed.date_only)
        return parsed

    def _gps(self, record: MetadataRecord) -> Candidate | None:
        d, t = record.get("GPS:GPSDateStamp"), record.get("GPS:GPSTimeStamp")
        if not isinstance(d, str) or not isinstance(t, str):
            return None
        p = parse_date_value(f"{d.replace('-', ':')} {t.split('.')[0]}Z")
        if p is None:
            return None
        return Candidate("GPS:GPSDateStamp", "GPS time (UTC, from satellites)", f"{d} {t}", p, 4, 0.60, "utc")

    # ------------------------------------------------------------- resolve
    def resolve(self, record: MetadataRecord, device: str | None = None) -> TimestampResult | None:
        cands = self.candidates(record, device)
        if not cands:
            return None
        best = cands[0]
        notes: list[str] = []
        conf = best.confidence
        assert best.interp is not None

        embedded = [c for c in cands if c.tier <= 4]
        confirmations = []
        for c in embedded:
            if c is best or c.source.split(" (")[0] == best.source.split(" (")[0]:
                continue
            if _same_moment(best, c):
                confirmations.append(c.label)
        if confirmations:
            notes.append("Confirmed by: " + "; ".join(dict.fromkeys(confirmations[:3])))
            conf = min(0.995, conf + 0.02)

        implied = self._implied_offset(best, cands)
        if implied:
            notes.append(implied)

        if best.tier <= 4:
            for c in embedded:
                if c is best or c.confidence < 0.75 or c.tier > 4:
                    continue
                gap = abs((c.interp.local - best.interp.local).total_seconds())
                if gap > 36 * 3600 and not _same_moment(best, c):
                    notes.append(f"Embedded dates disagree: {c.label} says {c.interp.local:%Y-%m-%d %H:%M:%S} "
                                 f"({gap / 86400:.1f} days apart). Please verify.")
                    conf = max(0.3, conf - 0.25)
                    break
        if best.tier == 5:
            notes.append("No embedded capture date was found; the time comes from the filename.")
        if best.tier == 6:
            notes.append("No embedded capture date was found. Using the file's modified time, which may "
                         "reflect copying or syncing rather than recording. Set the time manually if needed.")
        if best.parsed.date_only:
            notes.append("Only a date (no time of day) is recorded.")

        return TimestampResult(
            local=best.interp.local.replace(microsecond=0), offset=best.interp.offset,
            source=best.source, label=best.label, confidence=round(conf, 3),
            interpretation=best.interp.explanation, notes=notes, candidates=cands,
            is_fallback=best.tier == 6, from_filename=best.tier == 5)

    def _implied_offset(self, best: Candidate, cands: list[Candidate]) -> str | None:
        """For a timezone-less capture time, compare with the UTC movie header."""
        if best.parsed.offset is not None or best.semantics in ("utc", "instant"):
            return None
        header = next((c for c in cands if c.source == "QuickTime:CreateDate" and c.semantics == "utc"), None)
        if header is None:
            return None
        utc_wall = header.parsed.instant().replace(tzinfo=None) if header.parsed.instant() else header.parsed.wall
        diff = best.parsed.wall - utc_wall
        secs = diff.total_seconds()
        if abs(secs) > 14 * 3600 + 60:
            return None
        quarter = round(secs / 900) * 900
        if abs(secs - quarter) > 90:
            return None
        if quarter == 0:
            return ("The movie header (UTC) shows the same clock time, so this device may record UTC "
                    "without marking it. If times look shifted, enable 'treat timezone-less times as UTC'.")
        return (f"Recorded without a timezone; the UTC movie header implies the camera clock was at "
                f"{format_offset(dt.timedelta(seconds=quarter))}.")


def _same_moment(a: Candidate, b: Candidate, tol: float = 2.0) -> bool:
    ia, ib = _instant(a), _instant(b)
    if ia is not None and ib is not None:
        return abs((ia - ib).total_seconds()) <= tol
    return abs((a.parsed.wall - b.parsed.wall).total_seconds()) <= tol or \
        abs((a.interp.local - b.interp.local).total_seconds()) <= tol


def _instant(c: Candidate) -> dt.datetime | None:
    if c.semantics in ("utc", "instant"):
        return c.parsed.instant() or c.parsed.wall.replace(tzinfo=dt.timezone.utc)
    return c.parsed.instant()


def _glob(pattern: str, name: str) -> bool:
    import fnmatch
    return fnmatch.fnmatchcase(name, pattern)
