"""Optional ExifTool backend.

Takeline's native parsers are complete enough to run on their own. When an
ExifTool executable is bundled next to the app (vendor/exiftool/exiftool.exe),
it is used as a second opinion that adds manufacturer-specific tags the native
parsers do not decode. Values found natively always win on conflicts, so the
results never depend on whether ExifTool happens to be present.

ExifTool runs locally as a child process. It never touches the network.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .record import MetadataRecord

SKIP_GROUPS = {"System", "Composite", "ExifTool", "SourceFile"}
TIMEOUT_S = 60


def app_base_dir() -> Path:
    """Directory holding bundled resources, in development and in a PyInstaller build."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[3]


def locate_exiftool(configured: str | None = None) -> str | None:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    base = app_base_dir()
    for root in (base, Path(sys.executable).parent):
        candidates += [root / "vendor" / "exiftool" / "exiftool.exe",
                       root / "vendor" / "exiftool" / "exiftool(-k).exe",
                       root / "vendor" / "exiftool" / "exiftool"]
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    found = shutil.which("exiftool")
    return found


class ExifToolSession:
    """A persistent `exiftool -stay_open` process (one per scan)."""

    def __init__(self, exe: str):
        self.exe = exe
        self.proc: subprocess.Popen | None = None
        self.lines: "queue.Queue[str | None]" = queue.Queue()
        self.lock = threading.Lock()

    def _start(self) -> None:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.proc = subprocess.Popen(
            [self.exe, "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        self.lines = queue.Queue()
        t = threading.Thread(target=self._pump, args=(self.proc, self.lines), daemon=True)
        t.start()

    @staticmethod
    def _pump(proc: subprocess.Popen, q: "queue.Queue[str | None]") -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            q.put(raw.decode("utf-8", errors="replace"))
        q.put(None)

    def read(self, path: str) -> dict | None:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            assert self.proc and self.proc.stdin
            args = ["-json", "-a", "-G1", "-s", "-charset", "filename=utf8",
                    "-api", "LargeFileSupport=1", "-x", "System:all", path, "-execute"]
            try:
                self.proc.stdin.write(("\n".join(args) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
            except OSError:
                self.close()
                return None
            out: list[str] = []
            while True:
                try:
                    line = self.lines.get(timeout=TIMEOUT_S)
                except queue.Empty:
                    self.close()
                    return None
                if line is None:
                    self.proc = None
                    return None
                if line.strip() == "{ready}":
                    break
                out.append(line)
        text = "".join(out).strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return data[0] if isinstance(data, list) and data else None

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write(b"-stay_open\nFalse\n")
                proc.stdin.flush()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


def exiftool_record(session: ExifToolSession, path: str) -> MetadataRecord | None:
    data = session.read(path)
    if not data:
        return None
    rec = MetadataRecord(path=path)
    rec.backends.append("exiftool")
    for key, value in data.items():
        if ":" not in key:
            continue
        group, tag = key.split(":", 1)
        if group == "ExifTool" and tag in ("Warning", "Error"):
            rec.warn(f"ExifTool: {value}")
            continue
        if group in SKIP_GROUPS:
            continue
        if isinstance(value, list):
            value = "; ".join(str(v) for v in value)
        if isinstance(value, str) and value.startswith("(Binary data"):
            value = "<" + value.strip("()") + ">"
        rec.set(group, tag, value)
    return rec
