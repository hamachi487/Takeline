"""Scan -> extract -> detect -> resolve -> plan.

This is the dry run. Nothing here modifies any file; operations.executor is
the only module that writes to disk, and only after explicit confirmation.
"""

from __future__ import annotations

import datetime as dt
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .config import Settings
from .detection import DEVICE_CHOICES, DetectionEngine, DetectionResult
from .metadata import SUPPORTED_EXTENSIONS, MetadataExtractor, MetadataRecord
from .naming import FilenameTemplate, PlanRequest, find_duplicates, natural_key, plan_names
from .timestamps import CaptureTimestampResolver, TimestampResult, TimezonePolicy

IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
DEVICE_NAMES = dict(DEVICE_CHOICES)


@dataclass
class MediaItem:
    id: int
    path: Path
    size: int = 0
    record: MetadataRecord | None = None
    detection: DetectionResult | None = None
    timestamp: TimestampResult | None = None
    error: str | None = None
    duplicate_of: Path | None = None
    # user overrides
    device_override: str | None = None
    time_override: dt.datetime | None = None
    include: bool = True
    # plan
    proposed_name: str = ""
    suffix_n: int = 0
    unchanged: bool = False
    # result of the last operation
    result_status: str = ""
    result_path: str = ""
    result_error: str = ""

    # ------------------------------------------------------------ effective
    @property
    def device(self) -> str:
        if self.device_override:
            return self.device_override
        return self.detection.device if self.detection else "UNKNOWN"

    @property
    def device_name(self) -> str:
        if self.device_override:
            return DEVICE_NAMES.get(self.device_override, self.device_override) + " (manual)"
        return self.detection.display_name if self.detection else "Unknown"

    @property
    def model(self) -> str | None:
        if self.device_override:
            return None
        return self.detection.model if self.detection else None

    @property
    def device_confidence(self) -> float:
        if self.device_override:
            return 1.0
        return self.detection.confidence if self.detection else 0.0

    @property
    def capture_time(self) -> dt.datetime | None:
        if self.time_override:
            return self.time_override
        return self.timestamp.local if self.timestamp else None

    @property
    def time_source(self) -> str:
        if self.time_override:
            return "Set manually"
        return self.timestamp.source if self.timestamp else "—"

    @property
    def can_process(self) -> bool:
        return self.capture_time is not None

    @property
    def status(self) -> tuple[str, str]:
        """(level, text) where level is ok | review | error."""
        if self.result_status:
            lvl = "error" if self.result_status == "error" else "ok"
            return lvl, self.result_error or self.result_status.capitalize()
        if self.error:
            return "error", self.error
        if self.capture_time is None:
            return "error", "No capture date found; set the time manually"
        issues = []
        if self.timestamp and self.timestamp.is_fallback and not self.time_override:
            issues.append("Filesystem date only")
        if self.device == "UNKNOWN":
            issues.append("Unknown device")
        if self.duplicate_of is not None:
            issues.append(f"Same content as {self.duplicate_of.name}")
        if self.timestamp and not self.time_override and any("disagree" in n for n in self.timestamp.notes):
            issues.append("Embedded dates disagree")
        if self.unchanged:
            return ("review", "; ".join(issues)) if issues else ("ok", "Already named")
        if issues:
            return "review", "; ".join(issues)
        if self.suffix_n:
            return "ok", "Ready (name collision resolved)"
        return "ok", "Ready"


# ---------------------------------------------------------------- scanning
def collect_files(paths: Iterable[str | os.PathLike], recursive: bool = True,
                  cancel: threading.Event | None = None) -> tuple[list[Path], int]:
    """Expand dropped files/folders into supported media files. Returns (files, ignored_count)."""
    out: list[Path] = []
    seen: set[str] = set()
    ignored = 0

    def consider(p: Path):
        nonlocal ignored
        name = p.name
        if name.startswith("._") or name.casefold() in IGNORED_NAMES or name.startswith((".takeline-", ".capturesort-")):
            ignored += 1  # macOS AppleDouble files, system files, our temp files
            return
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            ignored += 1
            return
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    for raw in paths:
        p = Path(raw)
        if cancel is not None and cancel.is_set():
            break
        try:
            if p.is_dir():
                if recursive:
                    for root, dirs, files in os.walk(p, onerror=lambda e: None):
                        dirs[:] = [d for d in dirs if not d.startswith(".")]
                        for f in files:
                            consider(Path(root) / f)
                        if cancel is not None and cancel.is_set():
                            break
                else:
                    for child in p.iterdir():
                        if child.is_file():
                            consider(child)
            elif p.is_file():
                consider(p)
        except OSError:
            ignored += 1
    out.sort(key=lambda q: natural_key(str(q)))
    return out, ignored


# ---------------------------------------------------------------- analysis
class Analyzer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.extractor = MetadataExtractor(settings.use_exiftool, settings.exiftool_path or None)
        self.engine = DetectionEngine(threshold=settings.unknown_threshold)
        self.resolver = make_resolver(settings)

    def close(self) -> None:
        self.extractor.close()

    def analyze(self, item: MediaItem) -> MediaItem:
        try:
            item.size = item.path.stat().st_size
        except OSError:
            pass
        rec = self.extractor.extract(item.path)
        item.record = rec
        item.error = ("Could not read metadata: " + rec.errors[0].split(": ", 1)[-1]) if rec.errors else None
        if rec.errors and rec.errors[0].startswith("Could not access"):
            item.error = rec.errors[0]
        try:
            item.detection = self.engine.detect(rec)
        except Exception as exc:  # defensive: never kill the batch
            item.detection = DetectionResult("UNKNOWN", "Unknown", None, 0.0, [f"Detection failed: {exc}"])
        try:
            item.timestamp = self.resolver.resolve(rec, item.detection.device)
        except Exception as exc:
            item.timestamp = None
            item.error = item.error or f"Could not resolve capture time: {exc}"
        if item.error:
            item.include = False
        return item

    def analyze_many(self, items: list[MediaItem], progress: Callable[[int, int], None] | None = None,
                     cancel: threading.Event | None = None, workers: int = 4) -> None:
        done = 0
        lock = threading.Lock()
        # ExifTool runs as one persistent process; parallel native parsing still helps on slow disks.
        n_workers = 1 if self.extractor.exiftool_available else workers

        def job(it: MediaItem):
            nonlocal done
            if cancel is not None and cancel.is_set():
                return
            self.analyze(it)
            with lock:
                done += 1
                if progress:
                    progress(done, len(items))

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            list(pool.map(job, items))

    def mark_duplicates(self, items: list[MediaItem]) -> int:
        by_id = {it.id: it for it in items}
        dupes = find_duplicates((it.id, it.path, it.size) for it in items if not it.error)
        for it in items:
            it.duplicate_of = None
        for k, first in dupes.items():
            by_id[k].duplicate_of = by_id[first].path
        return len(dupes)


def make_resolver(settings: Settings) -> CaptureTimestampResolver:
    policy = TimezonePolicy(settings.tz_mode, settings.tz_zone, settings.naive_is_utc)
    profiles = {k: v for k, v in (settings.device_profiles or {}).items() if isinstance(v, dict)}
    return CaptureTimestampResolver(policy, profiles or None,
                                    allow_filename=settings.allow_filename_dates,
                                    allow_filesystem=settings.allow_filesystem_fallback)


def make_template(settings: Settings) -> FilenameTemplate:
    try:
        return FilenameTemplate(settings.template, settings.suffix_format, settings.ext_case)
    except ValueError:
        return FilenameTemplate()


def re_resolve(items: list[MediaItem], settings: Settings) -> None:
    """Re-run timestamp resolution after a timezone/settings change (no file re-read)."""
    resolver = make_resolver(settings)
    for it in items:
        if it.record is not None and it.detection is not None:
            try:
                it.timestamp = resolver.resolve(it.record, it.device)
            except Exception:
                it.timestamp = None


# ---------------------------------------------------------------- planning
@dataclass
class PlanStats:
    planned: int = 0
    collisions: int = 0
    unchanged: int = 0
    excluded: int = 0
    by_device: dict[str, int] = field(default_factory=dict)


def plan(items: list[MediaItem], settings: Settings, mode: str = "rename",
         destination: Path | None = None) -> PlanStats:
    """Compute proposed names. mode='rename' (in place) or 'copy' (into destination)."""
    template = make_template(settings)
    stats = PlanStats()
    reqs = []
    for it in items:
        it.proposed_name, it.suffix_n, it.unchanged = "", 0, False
        if not it.include or not it.can_process:
            stats.excluded += 1
            if it.can_process:
                # still show what the name would be
                it.proposed_name = template.render(it.device, it.capture_time, it.path.suffix, 0,
                                                   it.model, it.path.stem)
            continue
        target = destination if (mode == "copy" and destination) else it.path.parent
        reqs.append(PlanRequest(it.id, it.path, target, it.device, it.capture_time, it.path.suffix, it.model))
    results = plan_names(reqs, template, mode)
    by_id = {it.id: it for it in items}
    for key, res in results.items():
        it = by_id[key]
        it.proposed_name, it.suffix_n, it.unchanged = res.name, res.suffix_n, res.unchanged
        stats.planned += 1
        stats.collisions += 1 if res.collided else 0
        stats.unchanged += 1 if res.unchanged else 0
    for it in items:
        stats.by_device[it.device] = stats.by_device.get(it.device, 0) + 1
    return stats
