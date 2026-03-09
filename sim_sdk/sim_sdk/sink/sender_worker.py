"""
Background worker that drains the event buffer and sends batches
to the local record-agent.

Runs as a daemon thread so it does not prevent interpreter shutdown.
Best-effort: on failure it retries once briefly, then drops the batch
and increments failure counters.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, List, Optional

from .agent_client import AgentHttpClient, AgentUnavailableError
from .envelope import fixture_to_envelope
from .sender_metrics import SenderMetrics

if TYPE_CHECKING:
    from ..fixture.schema import FixtureEvent
    from .in_memory_buffer import InMemoryBuffer

logger = logging.getLogger(__name__)

_WARN_INTERVAL_S = 60.0


class SenderWorker:
    """Daemon thread that drains InMemoryBuffer and POSTs batches to the agent.

    Wake conditions:
        1. flush_interval_s timer expires (periodic sweep).
        2. notify() called by the sink when the buffer crosses the batch
           threshold.
        3. flush_sync() / stop() explicitly wake the thread.
    """

    def __init__(
        self,
        buffer: InMemoryBuffer,
        client: AgentHttpClient,
        metrics: SenderMetrics,
        *,
        service: str = "",
        flush_interval_s: float = 1.0,
        max_batch_events: int = 100,
        max_retries: int = 1,
    ):
        self._buffer = buffer
        self._client = client
        self._metrics = metrics
        self._service = service
        self._flush_interval_s = flush_interval_s
        self._max_batch_events = max_batch_events
        self._max_retries = max_retries

        self._wake = threading.Event()
        self._stop = threading.Event()

        self._flush_lock = threading.Lock()
        self._flush_waiters: List[threading.Event] = []

        self._last_warn_ts: float = 0.0
        self._thread: Optional[threading.Thread] = None

    # -- public API ----------------------------------------------------------

    def start(self) -> None:
        """Start the background sender thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="dopl-sender", daemon=True,
        )
        self._thread.start()

    def notify(self) -> None:
        """Wake the worker to drain the buffer immediately."""
        self._wake.set()

    def flush_sync(self, timeout_s: float = 5.0) -> bool:
        """Block until the buffer is drained and the in-flight send completes.

        Returns True if the flush completed within *timeout_s*.
        """
        done = threading.Event()
        with self._flush_lock:
            self._flush_waiters.append(done)
        self._wake.set()
        return done.wait(timeout=timeout_s)

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop the worker thread after draining remaining events."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- thread entry --------------------------------------------------------

    def _run(self) -> None:
        logger.debug(
            "SenderWorker started (interval=%.1fs, max_batch=%d)",
            self._flush_interval_s,
            self._max_batch_events,
        )
        while not self._stop.is_set():
            self._wake.wait(timeout=self._flush_interval_s)
            self._wake.clear()
            self._drain_and_send()
            self._signal_flush_waiters()

        # Final drain on shutdown
        self._drain_and_send()
        self._signal_flush_waiters()
        logger.debug("SenderWorker stopped")

    # -- internals -----------------------------------------------------------

    def _signal_flush_waiters(self) -> None:
        with self._flush_lock:
            waiters = list(self._flush_waiters)
            self._flush_waiters.clear()
        for w in waiters:
            w.set()

    def _drain_and_send(self) -> None:
        batch = self._buffer.drain()
        if not batch:
            return

        for i in range(0, len(batch), self._max_batch_events):
            chunk = batch[i : i + self._max_batch_events]
            self._send_chunk(chunk)

    def _send_chunk(self, events: List[FixtureEvent]) -> None:
        envelopes = [
            fixture_to_envelope(e, service=self._service) for e in events
        ]

        for attempt in range(1 + self._max_retries):
            try:
                resp = self._client.post_batch(envelopes)
                self._metrics.record_send(resp.accepted, resp.dropped)
                return

            except AgentUnavailableError:
                if attempt < self._max_retries:
                    time.sleep(0.1)
                    continue
                self._metrics.record_unavailable()
                self._metrics.record_drop(len(events))
                self._rate_limited_warn(
                    "Agent unavailable — dropped %d events", len(events),
                )
                return

            except Exception:
                if attempt < self._max_retries:
                    time.sleep(0.1)
                    continue
                self._metrics.record_failure()
                self._metrics.record_drop(len(events))
                logger.warning(
                    "Send failed after %d attempts — dropped %d events",
                    1 + self._max_retries,
                    len(events),
                    exc_info=True,
                )
                return

    def _rate_limited_warn(self, msg: str, *args: object) -> None:
        now = time.monotonic()
        if now - self._last_warn_ts >= _WARN_INTERVAL_S:
            logger.warning(msg, *args)
            self._last_warn_ts = now
