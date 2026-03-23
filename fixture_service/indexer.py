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
from typing import Any, Dict

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
    """Compute SHA-256 content hash of a fixture for deduplication.

    The hash covers the full fixture body using canonical JSON serialization
    (sorted keys, no whitespace) to ensure determinism regardless of key order.

    This is the deduplication identity per the architectural invariant:
    "Same hash = same fixture."

    Args:
        fixture: Parsed fixture dict.

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    canonical = json.dumps(fixture, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
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
    recorded_at = fixture.get("recorded_at", "") or event_time
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
