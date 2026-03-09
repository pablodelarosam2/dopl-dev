"""
AgentSink — concrete RecordSink that ships events to the local record-agent.

Wires together InMemoryBuffer + SenderWorker + AgentHttpClient.
The background sender thread is started automatically on construction
and stopped on close().
"""

from __future__ import annotations

import logging
from typing import List, TYPE_CHECKING

from .agent_client import AgentHttpClient
from .in_memory_buffer import DropPolicy
from .record_sink import RecordSink
from .sender_metrics import SenderMetrics
from .sender_worker import SenderWorker

if TYPE_CHECKING:
    from ..fixture.schema import FixtureEvent

logger = logging.getLogger(__name__)


class AgentSink(RecordSink):
    """RecordSink that sends events to the local dopl record-agent.

    Events flow:  emit() → InMemoryBuffer → SenderWorker → AgentHttpClient.

    The worker runs in a daemon thread and sends batches best-effort.
    If the agent is down the worker retries once, then drops the batch
    and increments failure counters.

    Usage::

        sink = AgentSink(agent_url="http://localhost:9700", service="my-app")
        ctx = init_sim(mode=SimMode.RECORD, sink=sink)
        # ... run application ...
        sink.close()   # flush + stop background thread
    """

    def __init__(
        self,
        agent_url: str = "http://localhost:9700",
        *,
        service: str = "",
        max_buffer_bytes: int = 2_000_000,
        max_batch_events: int = 100,
        flush_interval_s: float = 1.0,
        max_retries: int = 1,
        http_timeout_s: float = 5.0,
        drop_policy: DropPolicy = DropPolicy.DROP_OLDEST,
    ):
        super().__init__(
            max_buffer_bytes=max_buffer_bytes,
            max_batch_events=max_batch_events,
            drop_policy=drop_policy,
        )
        self._metrics = SenderMetrics()
        self._client = AgentHttpClient(agent_url, timeout_s=http_timeout_s)
        self._worker = SenderWorker(
            self._buffer,
            self._client,
            self._metrics,
            service=service,
            flush_interval_s=flush_interval_s,
            max_batch_events=max_batch_events,
            max_retries=max_retries,
        )
        self._worker.start()

    # -- RecordSink overrides ------------------------------------------------

    def emit(self, event: FixtureEvent) -> None:
        """Buffer an event and notify the worker if threshold reached.

        Non-blocking: never sends on the caller's thread.
        """
        self._buffer.append(event)
        self._metrics.record_buffer(1)
        if len(self._buffer) >= self._max_batch_events:
            self._worker.notify()

    def flush(self) -> None:
        """Drain the buffer and wait for the in-flight batch to complete."""
        self._worker.flush_sync()

    def close(self) -> None:
        """Flush remaining events and stop the background worker."""
        self._worker.stop()
        logger.debug("AgentSink closed — %s", self._metrics)

    def _persist_batch(self, batch: List[FixtureEvent]) -> None:
        pass

    # -- public accessors ----------------------------------------------------

    @property
    def metrics(self) -> SenderMetrics:
        """Access the sender pipeline counters."""
        return self._metrics
