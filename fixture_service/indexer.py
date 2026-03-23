"""
Indexer Service — SQS consumer that indexes fixtures from S3 into Postgres.

This is the middle stage of the three-stage fixture pipeline:
  Record (daemon) -> Index (this service) -> Retrieve (API)

The Indexer:
  1. Polls SQS for S3 object-creation event notifications.
  2. Downloads fixture JSON from S3.
  3. Extracts metadata (service, method, path, endpoint_key).
  4. Computes a SHA-256 content hash for deduplication.
  5. Inserts an index row into the fixtures_index Postgres table.

Architectural constraints (from CLAUDE.md):
  - The Indexer does NOT serve API requests — only consumes SQS and writes Postgres.
  - Never delete SQS messages on transient failures (Postgres down, S3 down).
  - Malformed fixtures are logged and discarded (delete SQS message, don't retry garbage).
  - Content-hash dedup makes processing idempotent (SQS at-least-once is safe).
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict

import psycopg2
import psycopg2.extras
from botocore.exceptions import ClientError

from fixture_service.config import IndexerConfig
from fixture_service.metrics import IndexerMetrics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class S3EventInfo:
    """Parsed metadata from an S3 event notification delivered via SQS."""

    s3_key: str
    bucket: str
    event_time: str


# ---------------------------------------------------------------------------
# S3 Event Parsing
# ---------------------------------------------------------------------------

def parse_s3_event(sqs_message: Dict[str, Any]) -> S3EventInfo:
    """Extract S3 key, bucket, and event time from an SQS message.

    The SQS message body contains a JSON-encoded S3 event notification
    with the structure::

        {"Records": [{"s3": {"bucket": {"name": "..."}, "object": {"key": "..."}}, ...}]}

    Args:
        sqs_message: Raw SQS message dict with at least a "Body" key.

    Returns:
        S3EventInfo with the parsed fields.

    Raises:
        ValueError: If the message body is not valid JSON, has no Records,
                    or is missing required S3 fields.
    """
    body_raw = sqs_message.get("Body", "")
    try:
        body = json.loads(body_raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Malformed SQS message body: {exc}") from exc

    records = body.get("Records")
    if not records:
        raise ValueError("No Records in S3 event notification")

    record = records[0]
    s3_info = record.get("s3", {})
    bucket = s3_info.get("bucket", {}).get("name", "")
    obj = s3_info.get("object")
    if not obj or "key" not in obj:
        raise ValueError("Missing s3 object key in event record")

    return S3EventInfo(
        s3_key=obj["key"],
        bucket=bucket,
        event_time=record.get("eventTime", ""),
    )


@dataclass(frozen=True)
class S3KeyMetadata:
    """Metadata extracted from a structured S3 fixture key.

    Key format: fixtures/{service}/{endpoint_key}/{date}/{fixture_id}.json
    """

    service: str
    endpoint_key: str
    date: str
    fixture_id: str


# ---------------------------------------------------------------------------
# S3 Key Parsing
# ---------------------------------------------------------------------------

def parse_s3_key(s3_key: str) -> S3KeyMetadata:
    """Extract service, endpoint_key, date, and fixture_id from a structured S3 key.

    Expected format::

        fixtures/{service}/{endpoint_key}/{date}/{fixture_id}.json

    Args:
        s3_key: The full S3 object key.

    Returns:
        S3KeyMetadata with parsed components.

    Raises:
        ValueError: If the key does not match the expected format.
    """
    parts = s3_key.split("/")
    if len(parts) < 5 or parts[0] != "fixtures":
        raise ValueError(
            f"Invalid S3 key format: expected 'fixtures/{{service}}/{{endpoint_key}}/{{date}}/{{id}}.json', "
            f"got '{s3_key}'"
        )

    service = parts[1]
    endpoint_key = parts[2]
    date = parts[3]
    filename = parts[4]
    fixture_id = filename.removesuffix(".json")

    return S3KeyMetadata(
        service=service,
        endpoint_key=endpoint_key,
        date=date,
        fixture_id=fixture_id,
    )


# ---------------------------------------------------------------------------
# Endpoint Key Generation
# ---------------------------------------------------------------------------

def build_endpoint_key(method: str, path: str) -> str:
    """Build a slugified endpoint key from HTTP method and path.

    Mirrors the daemon's key generation logic (Task 3.1 spec)::

        endpoint_key = f"{method}_{path}".lower().replace("/", "_").strip("_")

    Args:
        method: HTTP method (e.g., "POST", "GET").
        path: URL path (e.g., "/quote", "/checkout/status").

    Returns:
        Slugified endpoint key (e.g., "post_quote", "get_checkout_status").
    """
    raw = f"{method}_{path}".lower().replace("/", "_").strip("_")
    # Collapse consecutive underscores caused by leading slash: POST + _/quote -> post__quote -> post_quote
    while "__" in raw:
        raw = raw.replace("__", "_")
    return raw


# ---------------------------------------------------------------------------
# S3 Download and Parse
# ---------------------------------------------------------------------------

def download_and_parse(s3_client: Any, bucket: str, s3_key: str) -> Dict[str, Any]:
    """Download a fixture JSON file from S3 and parse it.

    Args:
        s3_client: A boto3 S3 client (or mock).
        bucket: S3 bucket name.
        s3_key: Full S3 object key.

    Returns:
        Parsed fixture as a dict.

    Raises:
        ValueError: If the S3 object body is not valid JSON.
        botocore.exceptions.ClientError: Propagated on S3 errors (e.g., NoSuchKey).
    """
    response = s3_client.get_object(Bucket=bucket, Key=s3_key)
    body_bytes = response["Body"].read()

    try:
        return json.loads(body_bytes)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Malformed fixture JSON from s3://{bucket}/{s3_key}: {exc}") from exc


# ---------------------------------------------------------------------------
# Content Hash
# ---------------------------------------------------------------------------

def compute_content_hash(fixture: Dict[str, Any]) -> str:
    """SHA-256 of the canonical request + stubs body for deduplication.

    Only fields that define the fixture's semantic identity are included.
    Metadata fields (recorded_at, fixture_id, duration_ms, run_id) are
    excluded because they differ between recordings of the same request.

    Args:
        fixture: Parsed fixture dict.

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    dedup_fields = {
        "input": fixture.get("input"),
        "stubs": fixture.get("stubs"),
        "method": fixture.get("method", ""),
        "path": fixture.get("path", ""),
    }
    canonical = json.dumps(dedup_fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FixtureMetadata:
    """Combined metadata for a fixture, extracted from S3 key + fixture JSON."""

    service: str
    method: str
    path: str
    endpoint_key: str
    recorded_at: str
    tags: Dict[str, Any]
    fixture_id: str


# ---------------------------------------------------------------------------
# Metadata Extraction
# ---------------------------------------------------------------------------

def extract_metadata(
    fixture: Dict[str, Any],
    key_meta: S3KeyMetadata,
    event_time: str = "",
) -> FixtureMetadata:
    """Extract metadata from a parsed fixture and its S3 key components.

    - service and endpoint_key come from the S3 key (the key is the contract).
    - method, path, recorded_at, and tags come from the fixture JSON body.
    - If recorded_at is missing from the fixture, falls back to event_time.

    Args:
        fixture: Parsed fixture dict.
        key_meta: Metadata parsed from the S3 key.
        event_time: Fallback timestamp from the S3 event notification.

    Returns:
        FixtureMetadata with all fields populated.
    """
    method = fixture.get("method", "")
    path = fixture.get("path", "")
    recorded_at_str = fixture.get("recorded_at") or event_time or ""
    if recorded_at_str:
        recorded_at = recorded_at_str  # Postgres can parse ISO 8601
    else:
        recorded_at = datetime.now(timezone.utc).isoformat()
    tags = fixture.get("tags", {}) if fixture.get("tags") is not None else {}

    return FixtureMetadata(
        service=key_meta.service,
        method=method,
        path=path,
        endpoint_key=key_meta.endpoint_key,
        recorded_at=recorded_at,
        tags=tags,
        fixture_id=key_meta.fixture_id,
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def is_duplicate(cursor: Any, content_hash: str, window_hours: int) -> bool:
    """Check if a fixture with this content_hash was already indexed within the dedup window.

    Executes::

        SELECT 1 FROM fixtures_index
        WHERE content_hash = %s
          AND recorded_at > now() - interval '%s hours'

    Args:
        cursor: A psycopg2 cursor (or mock).
        content_hash: SHA-256 hex digest of the canonical fixture.
        window_hours: Number of hours for the dedup window.

    Returns:
        True if a duplicate exists within the window, False otherwise.
    """
    cursor.execute(
        """
        SELECT 1 FROM fixtures_index
        WHERE content_hash = %s
          AND recorded_at > now() - make_interval(hours => %s)
        LIMIT 1
        """,
        (content_hash, window_hours),
    )
    return cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Index Row Insert
# ---------------------------------------------------------------------------

def insert_index_row(
    cursor: Any,
    metadata: FixtureMetadata,
    s3_key: str,
    content_hash: str,
    s3_bucket: str,
) -> None:
    """Insert a fixture index row into Postgres.

    Args:
        cursor: A psycopg2 cursor (or mock).
        metadata: Extracted fixture metadata.
        s3_key: Full S3 object key.
        content_hash: SHA-256 hex digest of the canonical fixture.
        s3_bucket: S3 bucket name (used to construct s3_uri).
    """
    s3_uri = f"s3://{s3_bucket}/{s3_key}"
    tags_value = psycopg2.extras.Json(metadata.tags)

    cursor.execute(
        """
        INSERT INTO fixtures_index
            (service, method, path, endpoint_key, content_hash, s3_uri, recorded_at, tags)
        VALUES
            (%(service)s, %(method)s, %(path)s, %(endpoint_key)s, %(content_hash)s,
             %(s3_uri)s, %(recorded_at)s, %(tags)s)
        """,
        {
            "service": metadata.service,
            "method": metadata.method,
            "path": metadata.path,
            "endpoint_key": metadata.endpoint_key,
            "content_hash": content_hash,
            "s3_uri": s3_uri,
            "recorded_at": metadata.recorded_at,
            "tags": tags_value,
        },
    )


# ---------------------------------------------------------------------------
# Daily Cap
# ---------------------------------------------------------------------------

def is_daily_cap_reached(
    cursor: Any,
    service: str,
    endpoint_key: str,
    max_per_day: int,
) -> bool:
    """Check if the daily fixture cap has been reached for a service+endpoint.

    Layer 2 sampling: prevents runaway storage growth by capping the number
    of fixtures indexed per endpoint per day.

    Args:
        cursor: A psycopg2 cursor (or mock).
        service: Service name.
        endpoint_key: Slugified endpoint key.
        max_per_day: Maximum fixtures per endpoint per day.

    Returns:
        True if the cap has been reached or exceeded, False otherwise.
    """
    cursor.execute(
        """
        SELECT COUNT(*) FROM fixtures_index
        WHERE service = %s
          AND endpoint_key = %s
          AND recorded_at >= CURRENT_DATE
        """,
        (service, endpoint_key),
    )
    count = cursor.fetchone()[0]
    return count >= max_per_day


# ---------------------------------------------------------------------------
# Single Message Processing
# ---------------------------------------------------------------------------

def process_message(
    sqs_message: Dict[str, Any],
    s3_client: Any,
    sqs_client: Any,
    db_conn: Any,
    config: IndexerConfig,
    metrics: IndexerMetrics,
) -> None:
    """Process a single SQS message through the full indexing pipeline.

    Pipeline steps:
      1. Parse S3 event from SQS message body.
      2. Download fixture JSON from S3.
      3. Parse S3 key for metadata (service, endpoint_key, date).
      4. Extract metadata from fixture body + S3 key.
      5. Compute content hash.
      6. Dedup check (skip if duplicate within window).
      7. Daily cap check (skip if cap reached).
      8. Insert index row.
      9. Delete SQS message on success.

    Error handling:
      - Malformed JSON (SQS body or fixture): log, delete message, increment parse_errors.
      - S3/Postgres transient errors: do NOT delete message, let SQS retry.

    Args:
        sqs_message: Raw SQS message dict.
        s3_client: boto3 S3 client.
        sqs_client: boto3 SQS client.
        db_conn: psycopg2 connection.
        config: Indexer configuration.
        metrics: Metrics counters.
    """
    receipt_handle = sqs_message.get("ReceiptHandle", "")
    message_id = sqs_message.get("MessageId", "")

    # --- Step 1: Parse S3 event from SQS ---
    try:
        event_info = parse_s3_event(sqs_message)
    except ValueError:
        logger.error("Malformed SQS message body, discarding", extra={"message_id": message_id})
        metrics.inc_parse_errors()
        _delete_message(sqs_client, config.sqs_queue_url, receipt_handle)
        return

    s3_key = event_info.s3_key

    # --- Step 2: Download fixture from S3 ---
    try:
        fixture = download_and_parse(s3_client, config.s3_bucket, s3_key)
    except ValueError:
        logger.error("Malformed fixture JSON, discarding", extra={"s3_key": s3_key})
        metrics.inc_parse_errors()
        _delete_message(sqs_client, config.sqs_queue_url, receipt_handle)
        return
    except ClientError as exc:
        logger.warning(
            "S3 download failed, leaving message for retry",
            extra={"s3_key": s3_key, "error": str(exc)},
        )
        return  # Do NOT delete — SQS will retry

    # --- Step 3: Parse S3 key ---
    try:
        key_meta = parse_s3_key(s3_key)
    except ValueError:
        logger.error("Invalid S3 key format, discarding", extra={"s3_key": s3_key})
        metrics.inc_parse_errors()
        _delete_message(sqs_client, config.sqs_queue_url, receipt_handle)
        return

    # --- Step 4: Extract metadata ---
    metadata = extract_metadata(fixture, key_meta, event_time=event_info.event_time)

    # --- Step 5: Compute content hash ---
    content_hash = compute_content_hash(fixture)

    # --- Steps 6-8: Dedup, cap check, insert (all need Postgres) ---
    try:
        with db_conn.cursor() as cursor:
            # Dedup check
            if is_duplicate(cursor, content_hash, config.dedup_window_hours):
                logger.info("Duplicate fixture, skipping", extra={"content_hash": content_hash[:16]})
                metrics.inc_messages_processed()
                metrics.inc_duplicates_skipped()
                _delete_message(sqs_client, config.sqs_queue_url, receipt_handle)
                return

            # Daily cap check
            if is_daily_cap_reached(
                cursor,
                metadata.service,
                metadata.endpoint_key,
                config.max_fixtures_per_endpoint_per_day,
            ):
                logger.info(
                    "Daily cap reached, skipping",
                    extra={
                        "service": metadata.service,
                        "endpoint_key": metadata.endpoint_key,
                    },
                )
                metrics.inc_messages_processed()
                metrics.inc_daily_cap_skipped()
                _delete_message(sqs_client, config.sqs_queue_url, receipt_handle)
                return

            # Insert
            insert_index_row(cursor, metadata, s3_key, content_hash, config.s3_bucket)
            db_conn.commit()

    except psycopg2.Error as exc:
        logger.warning(
            "Postgres error, leaving message for retry",
            extra={"error": str(exc), "s3_key": s3_key},
        )
        try:
            db_conn.rollback()
        except Exception:
            pass
        return  # Do NOT delete — SQS will retry

    metrics.inc_messages_processed()
    metrics.inc_rows_inserted()
    _delete_message(sqs_client, config.sqs_queue_url, receipt_handle)
    logger.info(
        "Fixture indexed",
        extra={
            "fixture_id": metadata.fixture_id,
            "service": metadata.service,
            "endpoint_key": metadata.endpoint_key,
        },
    )


def _delete_message(sqs_client: Any, queue_url: str, receipt_handle: str) -> None:
    """Delete an SQS message by receipt handle.

    Best-effort: logs but does not raise on failure.
    """
    try:
        sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    except Exception as exc:
        logger.error("Failed to delete SQS message", extra={"error": str(exc)})


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

def run_indexer(
    s3_client: Any,
    sqs_client: Any,
    db_conn: Any,
    config: IndexerConfig,
    metrics: IndexerMetrics,
) -> None:
    """Main SQS polling loop for the Indexer service.

    Runs indefinitely, polling SQS for S3 event notifications and processing
    each message through the indexing pipeline. Exits only on KeyboardInterrupt
    or an unrecoverable error.

    Args:
        s3_client: boto3 S3 client.
        sqs_client: boto3 SQS client.
        db_conn: psycopg2 connection.
        config: Indexer configuration.
        metrics: Metrics counters.
    """
    logger.info(
        "Indexer starting",
        extra={
            "queue_url": config.sqs_queue_url,
            "bucket": config.s3_bucket,
            "dedup_window_hours": config.dedup_window_hours,
        },
    )

    while True:
        response = sqs_client.receive_message(
            QueueUrl=config.sqs_queue_url,
            MaxNumberOfMessages=config.sqs_max_messages,
            WaitTimeSeconds=config.sqs_wait_seconds,
        )

        messages = response.get("Messages", [])
        if not messages:
            continue

        for msg in messages:
            process_message(msg, s3_client, sqs_client, db_conn, config, metrics)

        # Log metrics snapshot after each batch
        logger.info("Batch complete", extra={"metrics": metrics.snapshot()})
