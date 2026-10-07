"""Results summaries and exportable reports (CSV, JSON or plain text)."""

from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .. import __version__

if TYPE_CHECKING:  # pragma: no cover
    from ..pipeline import MediaItem


@dataclass
class Summary:
    action: str = "Preview"
    scanned: int = 0
    processed: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    collisions: int = 0
    duplicates: int = 0
    destination: str = ""
    manifest: str = ""
    error_lines: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        verb = {"Create Renamed Copies": "copied", "Rename Originals": "renamed",
                "Undo": "restored"}.get(self.action, "processed")
        out = [f"{self.scanned} files scanned",
               f"{self.processed} files successfully {verb}",
               f"{self.errors} error{'s' if self.errors != 1 else ''}",
               f"{self.collisions} filename collision{'s' if self.collisions != 1 else ''} resolved"]
        if self.unchanged:
            out.append(f"{self.unchanged} already had the right name")
        if self.skipped:
            out.append(f"{self.skipped} skipped")
        if self.duplicates:
            out.append(f"{self.duplicates} likely duplicate file{'s' if self.duplicates != 1 else ''} flagged")
        return out


def item_rows(items: list["MediaItem"]) -> list[dict]:
    rows = []
    for it in items:
        ts = it.timestamp
        level, status = it.status
        rows.append({
            "original_path": str(it.path),
            "original_name": it.path.name,
            "included": it.include,
            "device": it.device,
            "device_name": it.device_name,
            "model": it.model or "",
            "device_confidence": round(it.device_confidence, 3),
            "device_reasons": " | ".join(it.detection.reasons) if it.detection and not it.device_override else "Manual override",
            "capture_time": it.capture_time.strftime("%Y-%m-%d %H:%M:%S") if it.capture_time else "",
            "time_source": it.time_source,
            "time_confidence": round(ts.confidence, 3) if ts and not it.time_override else (1.0 if it.time_override else 0.0),
            "time_interpretation": ts.interpretation if ts and not it.time_override else ("Set manually" if it.time_override else ""),
            "time_notes": " | ".join(ts.notes) if ts and not it.time_override else "",
            "proposed_name": it.proposed_name,
            "result": it.result_status,
            "result_path": it.result_path,
            "status_level": level,
            "status": status,
            "duplicate_of": str(it.duplicate_of) if it.duplicate_of else "",
        })
    return rows


def export_report(path: Path, items: list["MediaItem"], summary: Summary | None = None) -> Path:
    rows = item_rows(items)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads UTF-8
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["original_path"])
            w.writeheader()
            w.writerows(rows)
    elif suffix == ".json":
        data = {"app": "Takeline", "version": __version__,
                "generated": dt.datetime.now().isoformat(timespec="seconds"),
                "summary": summary.__dict__ if summary else None, "files": rows}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        lines = [f"Takeline {__version__} report - {dt.datetime.now():%Y-%m-%d %H:%M:%S}", ""]
        if summary:
            lines += [f"Action: {summary.action}"] + summary.lines()
            if summary.destination:
                lines.append(f"Destination: {summary.destination}")
            if summary.manifest:
                lines.append(f"Undo record: {summary.manifest}")
            lines.append("")
        for r in rows:
            lines.append(f"{r['original_name']}  ->  {r['proposed_name'] or '(not processed)'}")
            lines.append(f"    device: {r['device_name']} ({r['device_confidence']:.0%})   "
                         f"time: {r['capture_time']} from {r['time_source']}")
            lines.append(f"    status: {r['status']}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
