"""
HTTP client interceptor for requests library.

Monkey-patches requests.Session.request to:
- Record: Call real endpoint, save response to stub store
- Replay: Return recorded response from stub store
"""

import json
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse, urlunparse

import requests
from requests import PreparedRequest, Response, Session

from sim_sdk.canonicalize import canonicalize, fingerprint
from sim_sdk.context import get_context
from sim_sdk.store import StubStore
from sim_sdk.trace import add_http_stub


# Store the original method
_original_request: Optional[Callable] = None

# Headers to exclude from fingerprinting
EXCLUDED_FINGERPRINT_HEADERS = {
    "authorization",
    "cookie",
    "x-request-id",
    "x-correlation-id",
    "x-trace-id",
    "user-agent",
    "date",
    "cache-control",
}


class StubMissError(Exception):
    """Raised when a stub is not found in replay mode."""

    def __init__(self, fingerprint: str, method: str, url: str):
        self.fingerprint = fingerprint
        self.method = method
        self.url = url
        super().__init__(
            f"No stub found for {method} {url} (fingerprint: {fingerprint})"
        )


def patch_requests() -> None:
    """
    Monkey-patch requests.Session.request for simulation.

    This patches the Session class to intercept all HTTP calls.
    """
    global _original_request

    if _original_request is not None:
        # Already patched
        return

    _original_request = Session.request
    Session.request = _patched_request


def unpatch_requests() -> None:
    """
    Restore the original requests.Session.request.
    """
    global _original_request

    if _original_request is None:
        return

    Session.request = _original_request
    _original_request = None


def is_patched() -> bool:
    """Check if requests is currently patched."""
    return _original_request is not None


def _patched_request(
    self: Session,
    method: str,
    url: str,
    **kwargs: Any,
) -> Response:
    """
    Patched request method that intercepts calls in simulation mode.
    """
    ctx = get_context()

    # If simulation is not active, use original method
    if not ctx.is_active:
        return _original_request(self, method, url, **kwargs)

    # Generate fingerprint for this request
    fp = _fingerprint_request(method, url, kwargs)
    ordinal = ctx.next_ordinal(f"http:{fp}")

    # In replay mode, return from stub store
    if ctx.is_replaying:
        return _replay_request(fp, ordinal, method, url, ctx)

    # In record mode, call real endpoint and save response
    if ctx.is_recording:
        return _record_request(self, method, url, kwargs, fp, ordinal, ctx)

    # Fallback to original
    return _original_request(self, method, url, **kwargs)


def _fingerprint_request(
    method: str,
    url: str,
    kwargs: Dict[str, Any],
) -> str:
    """
    Generate a fingerprint for an HTTP request.

    Args:
        method: HTTP method
        url: Request URL
        kwargs: Request kwargs (data, json, headers, etc.)

    Returns:
        Fingerprint string
    """
    # Normalize URL (remove query params order, etc.)
    parsed = urlparse(url)
    normalized_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        "",  # params
        parsed.query,  # keep query as-is for now
        "",  # fragment
    ))

    # Get body
    body = None
    if "json" in kwargs:
        body = kwargs["json"]
    elif "data" in kwargs:
        data = kwargs["data"]
        if isinstance(data, dict):
            body = data
        elif isinstance(data, str):
            try:
                body = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                body = data
        elif isinstance(data, bytes):
            try:
                body = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                body = data.hex()

    # Get relevant headers
    headers = {}
    if "headers" in kwargs:
        for key, value in kwargs["headers"].items():
            if key.lower() not in EXCLUDED_FINGERPRINT_HEADERS:
                headers[key.lower()] = value

    # Build fingerprint data
    fp_data = {
        "method": method.upper(),
        "url": normalized_url,
    }

    if body is not None:
        fp_data["body"] = body

    if headers:
        fp_data["headers"] = headers

    return fingerprint(fp_data)


def _replay_request(
    fp: str,
    ordinal: int,
    method: str,
    url: str,
    ctx,
) -> Response:
    """
    Replay a request from the stub store.

    Args:
        fp: Request fingerprint
        ordinal: Call ordinal
        method: HTTP method
        url: Request URL
        ctx: SimContext

    Returns:
        Response object built from stub

    Raises:
        StubMissError: If no stub is found
    """
    if ctx.stub_dir is None:
        raise StubMissError(fp, method, url)

    store = StubStore(ctx.stub_dir)

    # Try with ordinal first, then without
    stub_data = store.load_http(f"{fp}_{ordinal}")
    if stub_data is None:
        stub_data = store.load_http(fp)

    if stub_data is None:
        raise StubMissError(fp, method, url)

    # Build Response object from stub
    response = Response()
    response.status_code = stub_data.get("status_code", 200)
    response._content = _encode_body(stub_data.get("body"))
    response.headers.update(stub_data.get("headers", {}))
    response.url = url
    response.request = PreparedRequest()
    response.request.method = method
    response.request.url = url

    return response


def _record_request(
    session: Session,
    method: str,
    url: str,
    kwargs: Dict[str, Any],
    fp: str,
    ordinal: int,
    ctx,
) -> Response:
    """
    Record a request to the stub store.

    Args:
        session: requests Session
        method: HTTP method
        url: Request URL
        kwargs: Request kwargs
        fp: Request fingerprint
        ordinal: Call ordinal
        ctx: SimContext

    Returns:
        Actual Response from the real endpoint
    """
    # Make the real request
    response = _original_request(session, method, url, **kwargs)

    # Save to stub store
    if ctx.stub_dir is not None:
        store = StubStore(ctx.stub_dir)

        # Prepare response data for storage
        stub_data = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
        }

        # Get response body
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                stub_data["body"] = response.json()
            except (json.JSONDecodeError, ValueError):
                stub_data["body"] = response.text
        elif "text/" in content_type:
            stub_data["body"] = response.text
        else:
            # Store as hex for binary content
            stub_data["body"] = response.content.hex()
            stub_data["body_encoding"] = "hex"

        # Save with ordinal
        store.save_http(
            f"{fp}_{ordinal}",
            stub_data,
            metadata={
                "method": method,
                "url": url,
                "fingerprint": fp,
                "ordinal": ordinal,
            },
        )

        # Also add to trace collector for @sim_trace decorator support
        add_http_stub({
            "fingerprint": fp,
            "ordinal": ordinal,
            "method": method,
            "url": url,
            "status": stub_data["status_code"],
            "body": stub_data.get("body"),
        })

    return response


def _encode_body(body: Any) -> bytes:
    """
    Encode a body value to bytes for Response._content.
    """
    if body is None:
        return b""

    if isinstance(body, bytes):
        return body

    if isinstance(body, str):
        return body.encode("utf-8")

    if isinstance(body, dict) or isinstance(body, list):
        return json.dumps(body).encode("utf-8")

    return str(body).encode("utf-8")


# Context manager for temporary patching
class RequestsPatch:
    """
    Context manager for temporarily patching requests.

    Example:
        with RequestsPatch():
            response = requests.get("https://api.example.com/data")
    """

    def __enter__(self) -> "RequestsPatch":
        patch_requests()
        return self

    def __exit__(self, *args) -> None:
        unpatch_requests()
