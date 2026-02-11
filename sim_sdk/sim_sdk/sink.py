"""
RecordSink - Async non-blocking recording pipeline.

The recording pipeline must never add latency to the request path.
Uses an in-memory ring buffer with background flush to disk/S3.

Architecture:
    @sim_trace completes → FixtureEvent → ring buffer → background thread → disk/S3

Components:
    RecordSink (ABC): Interface for all sinks
    LocalSink: In-memory buffer → local disk
    S3Sink: In-memory buffer → local disk → S3 (in s3_sink.py)
"""

import atexit
import json
import logging
import os
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sim_sdk.trace import FixtureEvent

logger = logging.getLogger(__name__)


@dataclass
class SinkConfig:
    """Configuration for RecordSink."""
    buffer_size_kb: int = 512  # Max buffer size in KB
    flush_interval_ms: int = 200  # Flush every N ms
    max_events: int = 1000  # Max events in buffer
    output_dir: Optional[Path] = None  # For LocalSink
    service_name: str = "default"
    endpoint_name: str = "default"


class RecordSink(ABC):
    """
    Abstract base class for fixture event sinks.

    All implementations must be non-blocking on emit().
    """

    @abstractmethod
    def emit(self, event: "FixtureEvent") -> None:
        """
        Emit a fixture event to the sink.

        This method MUST be non-blocking. Events should be
        queued for async processing.

        Args:
            event: The fixture event to record
        """
        pass

    @abstractmethod
    def flush(self) -> None:
        """
        Force flush all pending events.

        Blocks until all events are written. Used by recording
        scripts to ensure all events are persisted before exit.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Close the sink and release resources.

        Should flush remaining events before closing.
        """
        pass

    def __enter__(self) -> "RecordSink":
        return self

    def __exit__(self, *args) -> None:
        self.close()


class LocalSink(RecordSink):
    """
    Local filesystem sink with async buffering.

    Uses an in-memory ring buffer with a background thread
    that flushes to disk periodically.

    Features:
        - Non-blocking emit() via queue
        - Background flush thread
        - Bounded buffer (drops oldest on overflow)
        - Configurable flush interval
    """

    def __init__(self, config: Optional[SinkConfig] = None):
        """
        Initialize the LocalSink.

        Args:
            config: Sink configuration. If None, uses defaults from env.
        """
        self.config = config or self._config_from_env()
        self._buffer: Deque["FixtureEvent"] = deque(maxlen=self.config.max_events)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._flush_event = threading.Event()
        self._dropped_count = 0

        # Ensure output directory exists
        if self.config.output_dir:
            self.config.output_dir.mkdir(parents=True, exist_ok=True)

        # Start background flush thread
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="sim-sink-flush",
        )
        self._flush_thread.start()

        # Register cleanup on exit
        atexit.register(self.close)

    def _config_from_env(self) -> SinkConfig:
        """Create config from environment variables."""
        output_dir = os.environ.get("SIM_STUB_DIR")
        return SinkConfig(
            buffer_size_kb=int(os.environ.get("SIM_BUFFER_SIZE_KB", "512")),
            flush_interval_ms=int(os.environ.get("SIM_FLUSH_INTERVAL_MS", "200")),
            output_dir=Path(output_dir) if output_dir else None,
            service_name=os.environ.get("SIM_SERVICE_NAME", "default"),
            endpoint_name=os.environ.get("SIM_ENDPOINT_NAME", "default"),
        )

    def emit(self, event: "FixtureEvent") -> None:
        """
        Emit a fixture event (non-blocking).

        If the buffer is full, the oldest event is dropped.
        """
        with self._lock:
            if len(self._buffer) >= self.config.max_events:
                # Drop oldest (deque handles this with maxlen)
                self._dropped_count += 1
                if self._dropped_count % 100 == 1:
                    logger.warning(
                        f"RecordSink buffer full, dropped {self._dropped_count} events"
                    )
            self._buffer.append(event)

    def flush(self) -> None:
        """
        Force flush all pending events (blocking).
        """
        self._flush_event.set()
        # Wait for flush to complete
        while True:
            with self._lock:
                if len(self._buffer) == 0:
                    break
            time.sleep(0.01)

    def close(self) -> None:
        """
        Close the sink, flushing remaining events.
        """
        self._stop_event.set()
        self.flush()
        if self._flush_thread.is_alive():
            self._flush_thread.join(timeout=5.0)

    def _flush_loop(self) -> None:
        """
        Background thread that periodically flushes the buffer.
        """
        flush_interval_sec = self.config.flush_interval_ms / 1000.0

        while not self._stop_event.is_set():
            # Wait for flush interval or explicit flush request
            self._flush_event.wait(timeout=flush_interval_sec)
            self._flush_event.clear()

            # Flush buffer
            self._do_flush()

        # Final flush on shutdown
        self._do_flush()

    def _do_flush(self) -> None:
        """
        Actually write events to disk.
        """
        if self.config.output_dir is None:
            return

        events_to_write: List["FixtureEvent"] = []

        with self._lock:
            while self._buffer:
                events_to_write.append(self._buffer.popleft())

        for event in events_to_write:
            self._write_event(event)

    def _write_event(self, event: "FixtureEvent") -> None:
        """
        Write a single fixture event to disk.

        Creates the per-fixture file structure:
            {output_dir}/fixtures/{fixture_id}/
                input.json
                golden_output.json
                stubs.json
                metadata.json
        """
        if self.config.output_dir is None:
            return

        try:
            fixture_files = event.to_fixture_files()

            # Create fixture directory
            fixture_dir = (
                self.config.output_dir
                / self.config.service_name
                / self.config.endpoint_name
                / event.fixture_id
            )
            fixture_dir.mkdir(parents=True, exist_ok=True)

            # Write individual files
            for filename, data in fixture_files.items():
                filepath = fixture_dir / f"{filename}.json"
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, default=str)

            logger.debug(f"Wrote fixture {event.fixture_id} to {fixture_dir}")

        except Exception as e:
            logger.error(f"Failed to write fixture {event.fixture_id}: {e}")

    @property
    def pending_count(self) -> int:
        """Number of events waiting to be flushed."""
        with self._lock:
            return len(self._buffer)

    @property
    def dropped_count(self) -> int:
        """Number of events dropped due to buffer overflow."""
        return self._dropped_count


# Global default sink instance
_default_sink: Optional[RecordSink] = None
_sink_lock = threading.Lock()


def get_default_sink() -> Optional[RecordSink]:
    """
    Get the default RecordSink instance.

    Creates one from environment variables if not set.
    """
    global _default_sink

    with _sink_lock:
        if _default_sink is None:
            stub_dir = os.environ.get("SIM_STUB_DIR")
            if stub_dir:
                config = SinkConfig(output_dir=Path(stub_dir))
                _default_sink = LocalSink(config)

        return _default_sink


def set_default_sink(sink: Optional[RecordSink]) -> None:
    """
    Set the default RecordSink instance.

    Args:
        sink: The sink to use, or None to clear
    """
    global _default_sink

    with _sink_lock:
        if _default_sink is not None and _default_sink is not sink:
            _default_sink.close()
        _default_sink = sink


def init_sink(
    output_dir: Optional[Path] = None,
    service_name: str = "default",
    endpoint_name: str = "default",
    buffer_size_kb: int = 512,
    flush_interval_ms: int = 200,
) -> LocalSink:
    """
    Initialize and set the default sink.

    Convenience function for setting up the recording pipeline.

    Args:
        output_dir: Directory to write fixtures to
        service_name: Service name for partitioning
        endpoint_name: Endpoint name for partitioning
        buffer_size_kb: Max buffer size in KB
        flush_interval_ms: Flush interval in milliseconds

    Returns:
        The configured LocalSink
    """
    config = SinkConfig(
        output_dir=output_dir,
        service_name=service_name,
        endpoint_name=endpoint_name,
        buffer_size_kb=buffer_size_kb,
        flush_interval_ms=flush_interval_ms,
    )
    sink = LocalSink(config)
    set_default_sink(sink)
    return sink
