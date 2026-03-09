"""
Thread-safe counters for the sender pipeline.
"""

from __future__ import annotations

import threading
from typing import Dict


class SenderMetrics:
    """Atomic counters tracking sender pipeline health.

    All mutators acquire an internal lock, so they are safe to call from
    both the emitting thread and the background sender thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.buffered: int = 0
        self.sent: int = 0
        self.dropped: int = 0
        self.failures: int = 0
        self.batches: int = 0
        self.agent_unavailable: int = 0

    def record_buffer(self, count: int) -> None:
        with self._lock:
            self.buffered += count

    def record_send(self, accepted: int, dropped: int) -> None:
        with self._lock:
            self.sent += accepted
            self.dropped += dropped
            self.batches += 1

    def record_drop(self, count: int) -> None:
        with self._lock:
            self.dropped += count

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1

    def record_unavailable(self) -> None:
        with self._lock:
            self.agent_unavailable += 1
            self.failures += 1

    def snapshot(self) -> Dict[str, int]:
        """Return a point-in-time copy of all counters."""
        with self._lock:
            return {
                "buffered": self.buffered,
                "sent": self.sent,
                "dropped": self.dropped,
                "failures": self.failures,
                "batches": self.batches,
                "agent_unavailable": self.agent_unavailable,
            }

    def __repr__(self) -> str:
        s = self.snapshot()
        return (
            f"SenderMetrics(sent={s['sent']}, dropped={s['dropped']}, "
            f"failures={s['failures']}, batches={s['batches']})"
        )
