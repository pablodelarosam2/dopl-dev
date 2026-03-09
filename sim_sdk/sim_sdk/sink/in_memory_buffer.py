"""
In-memory buffer for FixtureEvent objects with bounded size and drop policies.

Thread-safe: all mutations are protected by an internal lock so the
buffer can be shared between the emitting thread and the sender worker.
"""

from __future__ import annotations

import random
import sys
import threading
from enum import Enum
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ..fixture.schema import FixtureEvent


class DropPolicy(Enum):
    DROP_OLDEST = "DROP_OLDEST"
    DROP_NEWEST = "DROP_NEWEST"
    DROP_RANDOM = "DROP_RANDOM"
    DROP_NONE = "DROP_NONE"


class InMemoryBuffer:
    """Bounded in-memory queue of FixtureEvent objects.

    Tracks approximate memory usage and drops events according to the
    configured DropPolicy when the buffer exceeds max_buffer_bytes.
    """

    def __init__(self, max_buffer_bytes: int, drop_policy: DropPolicy = DropPolicy.DROP_OLDEST):
        self._lock = threading.Lock()
        self.buffer: List[FixtureEvent] = []
        self.max_buffer_bytes = max_buffer_bytes
        self.drop_policy = drop_policy

    def __len__(self) -> int:
        with self._lock:
            return len(self.buffer)

    def memory_usage(self) -> int:
        with self._lock:
            return sys.getsizeof(self.buffer)

    def append(self, event: FixtureEvent) -> None:
        with self._lock:
            if self._memory_usage_unlocked() >= self.max_buffer_bytes and self.buffer:
                self._drop()
            self.buffer.append(event)

    def drain(self) -> List[FixtureEvent]:
        """Remove and return all buffered events."""
        with self._lock:
            batch = list(self.buffer)
            self.buffer.clear()
            return batch

    def _memory_usage_unlocked(self) -> int:
        return sys.getsizeof(self.buffer)

    def _drop(self) -> None:
        if self.drop_policy == DropPolicy.DROP_OLDEST:
            self.buffer.pop(0)
        elif self.drop_policy == DropPolicy.DROP_NEWEST:
            self.buffer.pop()
        elif self.drop_policy == DropPolicy.DROP_RANDOM:
            self.buffer.pop(random.randint(0, len(self.buffer) - 1))
