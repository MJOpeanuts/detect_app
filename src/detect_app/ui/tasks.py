from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

# Model inspection, SHA-256 hashing and copies run here, never on the UI thread.
_TASK_POOL = QThreadPool()
_TASK_POOL.setMaxThreadCount(1)


class _TaskSignals(QObject):
    done = Signal(int, object, object)


class _Task(QRunnable):
    def __init__(self, token: int, function: Callable[[], object], signals: _TaskSignals):
        super().__init__()
        self._token = token
        self._function = function
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._function()
        except Exception as exc:  # reported to the UI thread as a diagnostic
            self._signals.done.emit(self._token, None, exc)
            return
        self._signals.done.emit(self._token, result, None)


class TaskRunner(QObject):
    """Runs callables in a background pool and reports (name, result, error) on the UI thread."""

    finished = Signal(str, object, object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._token = 0
        self._pending: dict[int, tuple[str, _TaskSignals]] = {}

    def run(self, name: str, function: Callable[[], object]) -> None:
        self._token += 1
        signals = _TaskSignals()
        signals.done.connect(self._done)
        self._pending[self._token] = (name, signals)
        _TASK_POOL.start(_Task(self._token, function, signals))

    def is_pending(self, name: str | None = None) -> bool:
        return any(name is None or pending_name == name for pending_name, _ in self._pending.values())

    def _done(self, token: int, result: object, error: object) -> None:
        entry = self._pending.pop(token, None)
        if entry is None:
            return
        self.finished.emit(entry[0], result, error)


def wait_for_tasks(milliseconds: int = 5000) -> bool:
    return _TASK_POOL.waitForDone(milliseconds)
