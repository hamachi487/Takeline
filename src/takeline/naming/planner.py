"""Plans final filenames without ever colliding with anything.

Guarantees:
* No two planned files share a name in the same folder (case-insensitive,
  because Windows file names are case-insensitive).
* No planned name equals a file already on disk that is not part of the batch.
* Deterministic: same inputs -> same names. Files with the same timestamp are
  ordered by capture time, then by original name (natural sort).
* Idempotent rename: running "Rename Originals" again on an already renamed
  folder changes nothing, because files that already carry one of their
  candidate names keep it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .template import FilenameTemplate


@dataclass
class PlanRequest:
    key: int
    src: Path
    target_dir: Path
    device: str
    when: dt.datetime
    ext: str
    model: str | None = None


@dataclass
class PlanResult:
    name: str
    suffix_n: int
    unchanged: bool          # rename mode: already has this exact name
    collided: bool           # a suffix was needed


def natural_key(text: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text.casefold())]


def default_listing(folder: Path) -> set[str]:
    try:
        return {n.casefold() for n in os.listdir(folder)}
    except OSError:
        return set()


def plan_names(requests: Iterable[PlanRequest], template: FilenameTemplate, mode: str,
               listing: Callable[[Path], set[str]] = default_listing) -> dict[int, PlanResult]:
    reqs = list(requests)
    by_dir: dict[str, list[PlanRequest]] = {}
    for r in reqs:
        by_dir.setdefault(os.path.normcase(str(r.target_dir.resolve() if r.target_dir.exists() else r.target_dir)), []).append(r)

    results: dict[int, PlanResult] = {}
    for dir_key, items in sorted(by_dir.items()):
        folder = items[0].target_dir
        on_disk = listing(folder)
        if mode == "rename":
            # Batch members currently in this folder will move; their names become free.
            leaving = {r.src.name.casefold() for r in items if _same_dir(r.src.parent, folder)}
            occupied = on_disk - leaving
        else:
            occupied = set(on_disk)
        taken: set[str] = set(occupied)

        def render(r: PlanRequest, n: int) -> str:
            return template.render(r.device, r.when, r.ext, n, r.model, r.src.stem)

        groups: dict[str, list[PlanRequest]] = {}
        for r in items:
            groups.setdefault(render(r, 0).casefold(), []).append(r)
        ordered_groups = sorted(groups.items())
        for _k, g in ordered_groups:
            g.sort(key=lambda r: (r.when, natural_key(r.src.stem), natural_key(r.src.name)))

        per_item = "{ORIGINAL}" in template.effective
        cache: dict[tuple[str, int], str] = {}

        def candidate(gkey: str, r: PlanRequest, n: int) -> str:
            if per_item:
                return render(r, n)
            k = (gkey, n)
            if k not in cache:
                cache[k] = render(r, n)
            return cache[k]

        # Pass 1 (rename mode): files already carrying one of their candidate names keep it.
        if mode == "rename":
            for gkey, g in ordered_groups:
                span = len(g) + 100
                lookup = None if per_item else {candidate(gkey, g[0], n).casefold(): n for n in range(span)}
                for r in g:
                    if not _same_dir(r.src.parent, folder):
                        continue
                    current = r.src.name.casefold()
                    if current in taken:
                        continue
                    if lookup is not None:
                        n = lookup.get(current)
                    else:
                        n = next((k for k in range(span) if render(r, k).casefold() == current), None)
                    if n is not None:
                        rendered = candidate(gkey, r, n)
                        taken.add(current)
                        # Same name (case-insensitively): keep it; only letter case may be fixed.
                        results[r.key] = PlanResult(rendered, n, rendered == r.src.name, n > 0)

        # Pass 2: everyone else gets the first free name. The per-group counter
        # only moves forward, so large same-second bursts stay linear.
        for gkey, g in ordered_groups:
            n = 0
            for r in g:
                if r.key in results:
                    continue
                while True:
                    name = candidate(gkey, r, n)
                    if name.casefold() not in taken:
                        break
                    n += 1
                    if n > 99_999:
                        raise RuntimeError(f"Could not find a free name for {r.src}")
                taken.add(name.casefold())
                unchanged = mode == "rename" and _same_dir(r.src.parent, folder) and name == r.src.name
                results[r.key] = PlanResult(name, n, unchanged, n > 0)
    return results


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except OSError:
        return False


# ------------------------------------------------------------- duplicates
def quick_fingerprint(path: Path, chunk: int = 1024 * 1024) -> str | None:
    """Size + first and last MiB. Only computed for files whose sizes match."""
    try:
        size = path.stat().st_size
        h = hashlib.sha1(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(chunk))
            if size > chunk:
                f.seek(max(chunk, size - chunk))
                h.update(f.read(chunk))
        return h.hexdigest()
    except OSError:
        return None


def find_duplicates(entries: Iterable[tuple[int, Path, int]]) -> dict[int, int]:
    """entries: (key, path, size). Returns {key: key_of_first_identical_file}."""
    by_size: dict[int, list[tuple[int, Path]]] = {}
    for key, path, size in entries:
        if size > 0:
            by_size.setdefault(size, []).append((key, path))
    dupes: dict[int, int] = {}
    for _size, group in by_size.items():
        if len(group) < 2:
            continue
        seen: dict[str, int] = {}
        for key, path in sorted(group, key=lambda kp: (natural_key(kp[1].stem), natural_key(str(kp[1])))):
            fp = quick_fingerprint(path)
            if fp is None:
                continue
            if fp in seen:
                dupes[key] = seen[fp]
            else:
                seen[fp] = key
    return dupes
