"""
S3Sink - Async recording pipeline to S3.

Extends LocalSink to upload fixtures to S3 after writing to local disk.

Architecture:
    FixtureEvent → in-memory buffer → local disk → S3 upload

S3 Partition Scheme:
    s3://{bucket}/
        {service}/
            {endpoint}/
                {fixture_id}/
                    input.json
                    stubs.json
                    golden_output.json
                    metadata.json
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from sim_sdk.sink import LocalSink, RecordSink, SinkConfig

if TYPE_CHECKING:
    from sim_sdk.trace import FixtureEvent

logger = logging.getLogger(__name__)


@dataclass
class S3SinkConfig(SinkConfig):
    """Configuration for S3Sink."""
    bucket: str = ""
    prefix: str = ""  # Optional prefix within bucket
    region: str = "us-east-1"
    local_cache_dir: Optional[Path] = None  # Local cache before upload
    upload_interval_ms: int = 5000  # Upload batch every N ms
    max_upload_batch: int = 50  # Max fixtures per upload batch


class S3Sink(RecordSink):
    """
    S3 sink with local buffering and batch uploads.

    Uses LocalSink for initial buffering, then uploads to S3
    in a separate background thread.

    Features:
        - Non-blocking emit() via LocalSink
        - Background S3 upload thread
        - Batch uploads for efficiency
        - Local cache as durability layer
    """

    def __init__(self, config: S3SinkConfig):
        """
        Initialize the S3Sink.

        Args:
            config: S3 sink configuration

        Requires:
            boto3 library for S3 access
        """
        self.config = config
        self._s3_client = None

        # Use local cache dir or temp directory
        local_dir = config.local_cache_dir or Path("/tmp/sim-cache")
        local_config = SinkConfig(
            output_dir=local_dir,
            service_name=config.service_name,
            endpoint_name=config.endpoint_name,
            buffer_size_kb=config.buffer_size_kb,
            flush_interval_ms=config.flush_interval_ms,
        )
        self._local_sink = LocalSink(local_config)

        # Track fixtures pending upload
        self._pending_uploads: List[Path] = []
        self._upload_lock = threading.Lock()
        self._stop_event = threading.Event()

        # Start upload thread
        self._upload_thread = threading.Thread(
            target=self._upload_loop,
            daemon=True,
            name="sim-s3-upload",
        )
        self._upload_thread.start()

    def _get_s3_client(self):
        """Lazy initialization of S3 client."""
        if self._s3_client is None:
            try:
                import boto3
                self._s3_client = boto3.client("s3", region_name=self.config.region)
            except ImportError:
                logger.error("boto3 not installed. Run: pip install boto3")
                raise
        return self._s3_client

    def emit(self, event: "FixtureEvent") -> None:
        """
        Emit a fixture event (non-blocking).

        Writes to local sink, which will be uploaded to S3 later.
        """
        self._local_sink.emit(event)

        # Track for S3 upload
        fixture_dir = (
            self._local_sink.config.output_dir
            / self.config.service_name
            / self.config.endpoint_name
            / event.fixture_id
        )
        with self._upload_lock:
            self._pending_uploads.append(fixture_dir)

    def flush(self) -> None:
        """
        Force flush to local disk and trigger S3 upload.
        """
        # Flush local sink first
        self._local_sink.flush()

        # Upload all pending
        self._do_upload(force_all=True)

    def close(self) -> None:
        """
        Close the sink, uploading remaining fixtures.
        """
        self._stop_event.set()
        self.flush()
        self._local_sink.close()
        if self._upload_thread.is_alive():
            self._upload_thread.join(timeout=30.0)

    def _upload_loop(self) -> None:
        """
        Background thread that periodically uploads to S3.
        """
        upload_interval_sec = self.config.upload_interval_ms / 1000.0

        while not self._stop_event.is_set():
            time.sleep(upload_interval_sec)
            self._do_upload()

        # Final upload on shutdown
        self._do_upload(force_all=True)

    def _do_upload(self, force_all: bool = False) -> None:
        """
        Upload pending fixtures to S3.
        """
        if not self.config.bucket:
            logger.warning("S3 bucket not configured, skipping upload")
            return

        # Get fixtures to upload
        with self._upload_lock:
            if force_all:
                to_upload = self._pending_uploads.copy()
                self._pending_uploads.clear()
            else:
                to_upload = self._pending_uploads[: self.config.max_upload_batch]
                self._pending_uploads = self._pending_uploads[self.config.max_upload_batch :]

        if not to_upload:
            return

        s3 = self._get_s3_client()

        for fixture_dir in to_upload:
            if not fixture_dir.exists():
                continue

            try:
                self._upload_fixture(s3, fixture_dir)
            except Exception as e:
                logger.error(f"Failed to upload {fixture_dir}: {e}")
                # Re-queue for retry
                with self._upload_lock:
                    self._pending_uploads.append(fixture_dir)

    def _upload_fixture(self, s3, fixture_dir: Path) -> None:
        """
        Upload a single fixture directory to S3.
        """
        # Build S3 key prefix
        relative_path = fixture_dir.relative_to(self._local_sink.config.output_dir)
        s3_prefix = f"{self.config.prefix}/{relative_path}" if self.config.prefix else str(relative_path)

        # Upload each file in the fixture directory
        for filepath in fixture_dir.glob("*.json"):
            s3_key = f"{s3_prefix}/{filepath.name}"

            with open(filepath, "rb") as f:
                s3.put_object(
                    Bucket=self.config.bucket,
                    Key=s3_key,
                    Body=f,
                    ContentType="application/json",
                )

            logger.debug(f"Uploaded {filepath.name} to s3://{self.config.bucket}/{s3_key}")

    @property
    def pending_count(self) -> int:
        """Number of fixtures pending S3 upload."""
        with self._upload_lock:
            return len(self._pending_uploads)


def init_s3_sink(
    bucket: str,
    service_name: str = "default",
    endpoint_name: str = "default",
    region: str = "us-east-1",
    prefix: str = "",
    local_cache_dir: Optional[Path] = None,
) -> S3Sink:
    """
    Initialize and set the default sink to S3Sink.

    Args:
        bucket: S3 bucket name
        service_name: Service name for partitioning
        endpoint_name: Endpoint name for partitioning
        region: AWS region
        prefix: Optional S3 key prefix
        local_cache_dir: Local directory for caching

    Returns:
        The configured S3Sink
    """
    from sim_sdk.sink import set_default_sink

    config = S3SinkConfig(
        bucket=bucket,
        prefix=prefix,
        region=region,
        service_name=service_name,
        endpoint_name=endpoint_name,
        local_cache_dir=local_cache_dir,
    )
    sink = S3Sink(config)
    set_default_sink(sink)
    return sink
