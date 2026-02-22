"""
In-memory buffer for FixtureEvent objects with bounded size and drop policies.
"""

from __future__ import annotations

import random
import sys
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
        self.buffer: List[FixtureEvent] = []
        self.max_buffer_bytes = max_buffer_bytes
        self.drop_policy = drop_policy

    def memory_usage(self) -> int:
        return sys.getsizeof(self.buffer)

    def append(self, event: FixtureEvent) -> None:
        if self.memory_usage() >= self.max_buffer_bytes and self.buffer:
            self._drop()
        self.buffer.append(event)

    def drain(self) -> List[FixtureEvent]:
        """Remove and return all buffered events."""
        batch = list(self.buffer)
        self.buffer.clear()
        return batch

    def _drop(self) -> None:
        if self.drop_policy == DropPolicy.DROP_OLDEST:
            self.buffer.pop(0)
        elif self.drop_policy == DropPolicy.DROP_NEWEST:
            self.buffer.pop()
        elif self.drop_policy == DropPolicy.DROP_RANDOM:
            self.buffer.pop(random.randint(0, len(self.buffer) - 1))
