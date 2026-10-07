"""Developer / diagnostic command line.

  python -m takeline inspect FILE [--json]        all metadata + detection + timestamp reasoning
  python -m takeline scan PATH... [--json]        dry-run preview table (changes nothing)
  python -m takeline apply PATH... --copy-to DIR  create renamed copies
  python -m takeline apply PATH... --rename --yes rename originals in place
  python -m takeline undo                         revert the last rename batch

The GUI is the normal way to use Takeline; this exists to inspect real
sample files when adding support for new cameras.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import Settings, history_dir
from .metadata.record import jsonable
from .operations import copy_batch, latest_manifest, rename_batch, undo_manifest
from .pipeline import Analyzer, MediaItem, collect_files, plan


def _settings(args) -> Settings:
    s = Settings.load(Path(args.config)) if getattr(args, "config", None) else Settings()
    if getattr(args, "tz", None):
        s.tz_mode = args.tz
    if getattr(args, "zone", None):
        s.tz_zone = args.zone
        s.tz_mode = "manual"
    if getattr(args, "no_exiftool", False):
        s.use_exiftool = False
    if getattr(args, "template", None):
        s.template = args.template
    return s


def cmd_inspect(args) -> int:
    s = _settings(args)
    an = Analyzer(s)
    try:
        for f in args.files:
            it = an.analyze(MediaItem(0, Path(f)))
            rec = it.record
            if args.json:
                print(json.dumps({
                    "metadata": rec.to_jsonable() if rec else None,
                    "detection": it.detection.to_dict() if it.detection else None,
                    "timestamp": it.timestamp.to_dict() if it.timestamp else None,
                }, indent=2, ensure_ascii=False, default=str))
                continue
            print("=" * 78)
            print(f"{it.path}")
            print(f"container: {rec.container}   backends: {', '.join(rec.backends) or 'none'}")
            for e in rec.errors:
                print(f"  ERROR   {e}")
            for w in rec.warnings:
                print(f"  warning {w}")
            for group, tags in rec.groups.items():
                print(f"[{group}]")
                for tag, value in tags.items():
                    print(f"    {tag:36s} {jsonable(value)}")
            d = it.detection
            print("-" * 78)
            print(f"DEVICE     {d.display_name} -> {d.device}   confidence {d.confidence:.0%}   model: {d.model or '-'}")
            for r in d.reasons:
                print(f"           - {r}")
            if d.alternatives:
                print("           also considered: " + ", ".join(f"{a} {c:.0%}" for a, c in d.alternatives))
            t = it.timestamp
            if t:
                print(f"CAPTURED   {t.display()}   from {t.source}   confidence {t.confidence:.0%}")
                print(f"           {t.interpretation}")
                for n in t.notes:
                    print(f"           note: {n}")
                print("CANDIDATES (in priority order)")
                for c in t.candidates:
                    print(f"   tier {c.tier}  {c.confidence:4.0%}  {c.source:34s} raw={c.raw!s:32.32s} -> "
                          f"{c.interp.local:%Y-%m-%d %H:%M:%S}  [{c.interp.explanation}]")
            else:
                print("CAPTURED   no usable date found")
    finally:
        an.close()
    return 0


def _scan(paths, s: Settings) -> list[MediaItem]:
    files, ignored = collect_files(paths, s.include_subfolders)
    if ignored:
        print(f"(ignored {ignored} unsupported or system files)", file=sys.stderr)
    items = [MediaItem(i, p) for i, p in enumerate(files)]
    an = Analyzer(s)
    try:
        an.analyze_many(items)
        an.mark_duplicates(items)
    finally:
        an.close()
    return items


def _print_table(items: list[MediaItem]) -> None:
    rows = sorted(items, key=lambda it: (it.capture_time is None, it.capture_time or 0, it.path.name))
    print(f"{'Original':34s} {'Device':16s} {'Capture time':19s} {'Source':26s} {'Conf':>4s}  New filename")
    for it in rows:
        ct = it.capture_time.strftime("%Y-%m-%d %H:%M:%S") if it.capture_time else "-"
        lvl, status = it.status
        flag = "" if lvl == "ok" else f"   [{status}]"
        print(f"{it.path.name[:34]:34s} {it.device_name[:16]:16s} {ct:19s} {it.time_source[:26]:26s} "
              f"{it.device_confidence:4.0%}  {it.proposed_name}{flag}")


def cmd_scan(args) -> int:
    s = _settings(args)
    items = _scan(args.paths, s)
    plan(items, s, "rename")
    if args.json:
        from .operations.report import item_rows
        print(json.dumps(item_rows(items), indent=2, ensure_ascii=False))
    else:
        _print_table(items)
        print(f"\n{len(items)} files. Nothing was changed (dry run).")
    return 0


def cmd_apply(args) -> int:
    s = _settings(args)
    items = _scan(args.paths, s)
    if args.copy_to:
        dest = Path(args.copy_to)
        plan(items, s, "copy", dest)
        jobs = [(it.id, it.path, dest / it.proposed_name) for it in items if it.include and it.proposed_name]
        results = copy_batch(jobs)
    else:
        if not args.yes:
            print("Refusing to rename originals without --yes. Run 'scan' first to review.", file=sys.stderr)
            return 2
        plan(items, s, "rename")
        jobs = [(it.id, it.path, it.path.with_name(it.proposed_name)) for it in items
                if it.include and it.proposed_name]
        results, manifest = rename_batch(jobs, history_dir())
        if manifest:
            print(f"Undo record: {manifest}")
    for r in results:
        print(f"{r.status:9s} {Path(r.src).name} -> {Path(r.dst).name} {r.error}")
    errors = sum(1 for r in results if r.status == "error")
    print(f"\n{len(results) - errors} ok, {errors} errors")
    return 1 if errors else 0


def cmd_undo(_args) -> int:
    m = latest_manifest(history_dir())
    if m is None:
        print("Nothing to undo.")
        return 0
    for r in undo_manifest(m):
        print(f"{r.status:9s} {Path(r.src).name} -> {Path(r.dst).name} {r.error}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="takeline", description=f"Takeline {__version__} (developer CLI)")
    ap.add_argument("--config", help="settings JSON to use instead of the saved settings")
    ap.add_argument("--tz", choices=["auto", "system", "manual"])
    ap.add_argument("--zone", help="IANA timezone for manual mode, e.g. America/Sao_Paulo")
    ap.add_argument("--no-exiftool", action="store_true", help="use only the built-in parsers")
    ap.add_argument("--template", help="filename template")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect", help="print all metadata and the reasoning for a file")
    p.add_argument("files", nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_inspect)
    p = sub.add_parser("scan", help="dry-run preview")
    p.add_argument("paths", nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_scan)
    p = sub.add_parser("apply", help="copy or rename")
    p.add_argument("paths", nargs="+")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--copy-to")
    g.add_argument("--rename", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_apply)
    p = sub.add_parser("undo", help="revert the last rename batch")
    p.set_defaults(func=cmd_undo)
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
