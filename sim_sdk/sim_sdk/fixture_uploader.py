"""
S3 structured key generation for the fixture pipeline.

Produces keys matching the contract:
    fixtures/{service}/{endpoint_key}/{date}/{fixture_id}.json

This module is used by:
  - The Indexer (Task 3.4) to compute endpoint_key from fixture metadata.
  - Tests to verify key structure.

The Go agent implements the same logic natively (agent/internal/uploader/s3key.go)
because it is the process that actually uploads to S3.

Zero framework dependencies -- standard library only.
"""

import re
from datetime import datetime


def build_endpoint_key(method: str, path: str) -> str:
    """Slugify HTTP method + path into a stable endpoint key.

    Rules (from Phase 3 spec, Section 3):
      - Lowercase
      - Slashes replaced with underscores
      - Consecutive underscores collapsed to one
      - Leading/trailing underscores stripped

    Examples:
        >>> build_endpoint_key("POST", "/quote")
        'post_quote'
        >>> build_endpoint_key("GET", "/checkout/status")
        'get_checkout_status'
    """
    raw = f"{method}_{path}".lower().replace("/", "_")
    raw = re.sub(r"_+", "_", raw)
    raw = raw.strip("_")
    return raw


def build_s3_key(
    service: str,
    method: str,
    path: str,
    fixture_id: str,
    recorded_at: datetime,
) -> str:
    """Build the structured S3 object key for a fixture.

    Layout (from Phase 3 spec, Section 3):
        fixtures/{service}/{endpoint_key}/{date}/{fixture_id}.json

    Args:
        service: Service name from sim.yaml (e.g. 'pricing-api').
        method: HTTP method (e.g. 'POST').
        path: URL path (e.g. '/quote').
        fixture_id: UUID assigned at recording time.
        recorded_at: Timestamp of recording (used for date partition).

    Returns:
        Full S3 object key string.
    """
    endpoint_key = build_endpoint_key(method, path)
    date_str = recorded_at.strftime("%Y-%m-%d")
    return f"fixtures/{service}/{endpoint_key}/{date_str}/{fixture_id}.json"
