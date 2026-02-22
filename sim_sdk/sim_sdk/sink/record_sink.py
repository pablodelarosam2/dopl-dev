"""
Buffered RecordSink base — concrete subclass of RecordSink that adds
an in-memory event buffer with configurable drop and flush policies.

Subclasses must implement _persist_batch() to write events to their
backing store (filesystem, S3, network, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List

from .in_memory_buffer import DropPolicy, InMemoryBuffer

if TYPE_CHECKING:
    from ..fixture.schema import FixtureEvent


class RecordSink(ABC):
    """RecordSink with an in-memory buffer and batch flushing.

    Events are buffered until max_batch_events is reached or flush()
    is called explicitly. Bounded memory and drop behaviour are fully
    delegated to InMemoryBuffer.
    """

    def __init__(
        self,
        *,
        max_buffer_bytes: int = 1_000_000,
        max_batch_events: int = 100,
        drop_policy: DropPolicy = DropPolicy.DROP_OLDEST,
    ):
        self._buffer = InMemoryBuffer(max_buffer_bytes, drop_policy)
        self._max_batch_events = max_batch_events

    def emit(self, event: FixtureEvent) -> None:
        self._buffer.append(event)
        if len(self._buffer.buffer) >= self._max_batch_events:
            self.flush()

    def flush(self) -> None:
        batch = self._buffer.drain()
        if batch:
            self._persist_batch(batch)

    def close(self) -> None:
        self.flush()

    @abstractmethod
    def _persist_batch(self, batch: List[FixtureEvent]) -> None:
        """Write a batch of events to the backing store."""


__all__ = ['RecordSink', 'DropPolicy']
