"""Run long work (scanning, copying, renaming) off the UI thread."""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot


class Task(QObject):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[threading.Event, Callable[[int, int, str], None]], Any]):
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(self.cancel, lambda a, b, c="": self.progress.emit(a, b, c))
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.done.emit(result)


class TaskRunner(QObject):
    """Owns at most one running task and its thread.

    Task signals are connected to slots of this object (which lives in the UI
    thread), so callbacks always run on the UI thread.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread: QThread | None = None
        self.task: Task | None = None
        self._cbs: tuple = (None, None, None)

    @property
    def busy(self) -> bool:
        return self.thread is not None

    def start(self, fn, on_progress, on_done, on_failed) -> Task:
        thread = QThread()
        task = Task(fn)
        task.moveToThread(thread)
        thread.started.connect(task.run)
        task.progress.connect(self._on_progress)
        task.done.connect(self._on_done)
        task.failed.connect(self._on_failed)
        self.thread, self.task = thread, task
        self._cbs = (on_progress, on_done, on_failed)
        thread.start()
        return task

    @Slot(int, int, str)
    def _on_progress(self, a: int, b: int, text: str) -> None:
        if self._cbs[0]:
            self._cbs[0](a, b, text)

    @Slot(object)
    def _on_done(self, result) -> None:
        self._finish(self._cbs[1], result)

    @Slot(str)
    def _on_failed(self, error: str) -> None:
        self._finish(self._cbs[2], error)

    def _finish(self, cb, value) -> None:
        thread, self.thread, self.task = self.thread, None, None
        if thread is not None:
            thread.quit()
            thread.wait()
            thread.deleteLater()
        if cb:
            cb(value)

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel.set()
