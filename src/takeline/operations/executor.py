"""The only code that writes to disk.

Safety rules:
* Never overwrite: copies are created with exclusive-create mode ('xb');
  renames check the target immediately before moving and refuse if it exists.
* Copies are verified (size) and timestamps are preserved; a failed copy
  leaves no partial file behind.
* In-place renames handle chains and cycles (A->B while B->C, or A<->B)
  without ever overwriting, using a temporary name only when a true cycle
  exists.
* Every rename batch writes an undo manifest BEFORE touching any file, so the
  batch can be reverted (Tools > Undo last rename) even after a crash.
* One failing file never stops the rest of the batch.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

CHUNK = 4 * 1024 * 1024


@dataclass
class OpResult:
    key: int
    src: str
    dst: str
    status: str        # copied | renamed | unchanged | skipped | error
    error: str = ""


class CancelledError(Exception):
    pass


def _err(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        return "Permission denied, or the file is open in another program"
    if isinstance(exc, FileExistsError):
        return "A file with the target name already exists (left untouched)"
    if isinstance(exc, FileNotFoundError):
        return "The original file was moved or deleted"
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 32:
        return "The file is in use by another program"
    if isinstance(exc, OSError) and exc.errno == 28:
        return "Not enough disk space"
    return f"{type(exc).__name__}: {exc}"


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


# ----------------------------------------------------------------- copying
def copy_one(src: Path, dst: Path, cancel: threading.Event | None = None,
             on_bytes: Callable[[int], None] | None = None) -> Path:
    """Copy src to dst without overwriting. Returns the path actually written.

    The source is opened first, so a missing or locked original fails before
    anything is created in the destination. The destination handle is always
    closed before cleanup (Windows cannot delete a file that is still open).
    """
    with open(src, "rb") as inp:
        dst.parent.mkdir(parents=True, exist_ok=True)
        target = dst
        for attempt in range(100):
            try:
                out = open(target, "xb")
                break
            except FileExistsError:
                # Appeared after planning (another program?): choose a fresh name, never overwrite.
                target = dst.with_name(f"{dst.stem}-dup{attempt + 1:02d}{dst.suffix}")
        else:
            raise FileExistsError(str(dst))
        ok = False
        try:
            try:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise CancelledError()
                    buf = inp.read(CHUNK)
                    if not buf:
                        break
                    out.write(buf)
                    if on_bytes:
                        on_bytes(len(buf))
            finally:
                out.close()
            shutil.copystat(src, target)
            if os.path.getsize(src) != os.path.getsize(target):
                raise OSError("Copy verification failed (size mismatch)")
            ok = True
            return target
        finally:
            if not ok:
                try:
                    os.remove(target)
                except OSError:
                    pass


def copy_batch(plan: list[tuple[int, Path, Path]], progress: Callable[[int, int, str], None] | None = None,
               cancel: threading.Event | None = None) -> list[OpResult]:
    results: list[OpResult] = []
    total = len(plan)
    for i, (key, src, dst) in enumerate(plan, 1):
        if cancel is not None and cancel.is_set():
            results.append(OpResult(key, str(src), str(dst), "skipped", "Cancelled"))
            continue
        if progress:
            progress(i - 1, total, src.name)
        try:
            written = copy_one(src, dst, cancel)
            note = "" if written == dst else f"Saved as {written.name}: a file named {dst.name} appeared during copying"
            results.append(OpResult(key, str(src), str(written), "copied", note))
        except CancelledError:
            results.append(OpResult(key, str(src), str(dst), "skipped", "Cancelled"))
        except Exception as exc:
            results.append(OpResult(key, str(src), str(dst), "error", _err(exc)))
    if progress:
        progress(total, total, "")
    return results


# ---------------------------------------------------------------- renaming
def write_manifest(history: Path, entries: list[dict]) -> Path:
    history.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = history / f"rename-{stamp}-{uuid.uuid4().hex[:6]}.json"
    _save_manifest(path, entries)
    return path


def _save_manifest(path: Path, entries: list[dict]) -> None:
    data = {"app": "Takeline", "version": 1, "created": dt.datetime.now().isoformat(timespec="seconds"),
            "entries": entries}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _execute_moves(moves: list[dict], finish: Callable[[dict, str, str], None],
                   cancel: threading.Event | None = None,
                   on_park: Callable[[dict], None] | None = None) -> None:
    """Move every m['cur'] to m['dst'] without overwriting anything.

    Targets occupied by another pending move wait until that move happens;
    cycles are broken by parking one file on a temporary name. A target held
    by a file outside the batch is an error for that file only.
    """
    pending = list(moves)
    while pending:
        if cancel is not None and cancel.is_set():
            for m in pending:
                if m["cur"] != m["src"]:  # parked on a temp name: put it back
                    try:
                        os.rename(m["cur"], m["src"])
                        m["cur"] = m["src"]
                    except OSError:
                        pass
                finish(m, "skipped", "Cancelled")
            return
        progressed = False
        for m in list(pending):
            dst: Path = m["dst"]
            if os.path.exists(dst) and not _same_file(m["cur"], dst):
                continue  # occupied (maybe by a pending move); retry later
            try:
                os.rename(m["cur"], dst)
                m["cur"] = dst
                finish(m, "renamed", "")
            except Exception as exc:
                if m["cur"] != m["src"]:
                    try:
                        os.rename(m["cur"], m["src"])
                        m["cur"] = m["src"]
                    except OSError:
                        pass
                finish(m, "error", _err(exc))
            pending.remove(m)
            progressed = True
        if progressed:
            continue
        current = {os.path.normcase(str(q["cur"])): q for q in pending}
        m = pending[0]
        if os.path.normcase(str(m["dst"])) not in current:
            pending.remove(m)
            if m["cur"] != m["src"]:
                try:
                    os.rename(m["cur"], m["src"])
                    m["cur"] = m["src"]
                except OSError:
                    pass
            finish(m, "error", "A file with the target name already exists (left untouched)")
            continue
        temp = m["cur"].with_name(f".takeline-tmp-{uuid.uuid4().hex[:10]}{m['cur'].suffix}")
        try:
            os.rename(m["cur"], temp)
            m["cur"] = temp
            if on_park:
                on_park(m)
        except Exception as exc:
            pending.remove(m)
            finish(m, "error", _err(exc))


def rename_batch(plan: list[tuple[int, Path, Path]], history: Path,
                 progress: Callable[[int, int, str], None] | None = None,
                 cancel: threading.Event | None = None) -> tuple[list[OpResult], Path | None]:
    """Rename files in place. plan entries are (key, src, dst) with dst in src's folder."""
    results: dict[int, OpResult] = {}
    moves = []
    for key, src, dst in plan:
        if str(src) == str(dst):
            results[key] = OpResult(key, str(src), str(dst), "unchanged")
        else:
            moves.append({"key": key, "src": src, "cur": src, "dst": dst})
    if not moves:
        return [results[k] for k, _s, _d in plan], None

    entries = [{"from": str(m["src"]), "to": str(m["dst"]), "via": None} for m in moves]
    manifest = write_manifest(history, entries)  # written BEFORE any file is touched
    index = {m["key"]: i for i, m in enumerate(moves)}
    total = len(plan)
    counter = [len(results)]

    def finish(m, status, error):
        final = m["dst"] if status == "renamed" else m["cur"]
        results[m["key"]] = OpResult(m["key"], str(m["src"]), str(final), status, error)
        entries[index[m["key"]]]["status"] = status
        counter[0] += 1
        if counter[0] % 25 == 0:  # keep the undo record current in case the app is stopped
            _save_manifest(manifest, entries)
        if progress:
            progress(counter[0], total, m["src"].name)

    def on_park(m):
        entries[index[m["key"]]]["via"] = str(m["cur"])
        _save_manifest(manifest, entries)

    _execute_moves(moves, finish, cancel, on_park)
    _save_manifest(manifest, entries)
    return [results[k] for k, _s, _d in plan], manifest


def latest_manifest(history: Path) -> Path | None:
    try:
        files = sorted(history.glob("rename-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not data.get("undone"):
                return f
        except (OSError, ValueError):
            continue
    return None


def undo_manifest(manifest: Path) -> list[OpResult]:
    """Rename files back to their original names (never overwriting anything)."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    results: list[OpResult] = []
    moves = []
    for i, e in enumerate(data.get("entries", [])):
        src, dst, via = Path(e["from"]), Path(e["to"]), e.get("via")
        status = e.get("status")  # missing only if the app stopped mid-batch
        if status is not None and status != "renamed":
            # Never touch a target that this batch did not create: it may be someone else's file.
            results.append(OpResult(i, str(dst), str(src), "skipped", "Was not renamed by that batch"))
            continue
        current = next((c for c in (dst, Path(via) if via else None) if c is not None and c.exists()), None)
        if current is None:
            results.append(OpResult(i, str(dst), str(src), "skipped",
                                    "Not renamed by that batch, or no longer there"))
        elif str(current) == str(src):
            results.append(OpResult(i, str(current), str(src), "unchanged"))
        else:
            moves.append({"key": i, "src": current, "cur": current, "dst": src})

    def finish(m, status, error):
        results.append(OpResult(m["key"], str(m["src"]), str(m["dst"] if status == "renamed" else m["cur"]),
                                status, error))

    _execute_moves(moves, finish)
    data["undone"] = dt.datetime.now().isoformat(timespec="seconds")
    try:
        manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return sorted(results, key=lambda r: r.key)


def as_dicts(results: list[OpResult]) -> list[dict]:
    return [asdict(r) for r in results]
