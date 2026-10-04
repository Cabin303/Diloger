"""Qt event-loop clock. Timer anchoring is the Session Engine's job."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal


class QtClock(QObject):
    ticked = Signal()

    def __init__(self, tick_interval_ms: int = 100, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(tick_interval_ms)
        self._timer.timeout.connect(self.ticked.emit)
        self._timer.start()
        self._deadline: Optional[float] = None
        self._pending: Optional[QTimer] = None

    def now(self) -> float:
        import time

        return time.monotonic()

    def schedule(self, delay: float, callback: Callable[[], None]) -> None:
        self.cancel()
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.timeout.connect(callback)
        self._deadline = self.now() + delay
        self._pending.start(int(delay * 1000))

    def cancel(self) -> None:
        if self._pending is not None:
            self._pending.stop()
            self._pending.deleteLater()
        self._pending = None
        self._deadline = None
