"""
JSON canonicalization for stable fingerprinting.

Ensures that equivalent data structures produce identical fingerprints
regardless of key ordering, whitespace, or minor float variations.
"""

import hashlib
import json
from decimal import Decimal
from typing import Any, Union


def canonicalize(data: Any) -> str:
    """
    Convert data to a canonical JSON string.

    - Sorts dictionary keys alphabetically (recursively)
    - Normalizes floats to 6 decimal places
    - Converts Decimal to float
    - Handles None/null consistently
    - Removes extra whitespace

    Args:
        data: Any JSON-serializable data structure

    Returns:
        Canonical JSON string representation
    """
    normalized = _normalize_value(data)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def fingerprint(data: Any) -> str:
    """
    Generate a stable fingerprint (hash) of the data.

    Args:
        data: Any JSON-serializable data structure

    Returns:
        First 16 characters of SHA256 hash of canonicalized data
    """
    canonical = canonicalize(data)
    hash_obj = hashlib.sha256(canonical.encode("utf-8"))
    return hash_obj.hexdigest()[:16]


def _normalize_value(value: Any) -> Any:
    """
    Recursively normalize a value for canonical representation.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        # Must check bool before int since bool is subclass of int
        return value

    if isinstance(value, (int,)):
        return value

    if isinstance(value, float):
        # Round to 6 decimal places for stability
        if value != value:  # NaN check
            return None
        if value == float("inf"):
            return "Infinity"
        if value == float("-inf"):
            return "-Infinity"
        return round(value, 6)

    if isinstance(value, Decimal):
        return round(float(value), 6)

    if isinstance(value, str):
        return value

    if isinstance(value, bytes):
        # Encode bytes as base64 string
        import base64
        return base64.b64encode(value).decode("ascii")

    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]

    # For other types, try to convert to string
    return str(value)


def fingerprint_request(
    method: str,
    path: str,
    body: Any = None,
    headers: dict = None,
    header_keys: list = None,
) -> str:
    """
    Generate a fingerprint for an HTTP request.

    Args:
        method: HTTP method (GET, POST, etc.)
        path: Request path
        body: Request body (optional)
        headers: Request headers dict (optional)
        header_keys: List of header keys to include in fingerprint (optional)

    Returns:
        Fingerprint string
    """
    data = {
        "method": method.upper(),
        "path": path,
    }

    if body is not None:
        data["body"] = body

    if headers and header_keys:
        selected_headers = {
            k: headers.get(k)
            for k in header_keys
            if k in headers
        }
        if selected_headers:
            data["headers"] = selected_headers

    return fingerprint(data)


def fingerprint_sql(sql: str, params: Union[tuple, list, dict] = None) -> str:
    """
    Generate a fingerprint for a SQL query.

    Args:
        sql: SQL query string
        params: Query parameters

    Returns:
        Fingerprint string
    """
    # Normalize SQL whitespace
    normalized_sql = " ".join(sql.split())

    data = {"sql": normalized_sql}
    if params is not None:
        data["params"] = params

    return fingerprint(data)
