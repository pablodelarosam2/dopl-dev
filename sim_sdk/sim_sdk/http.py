"""
sim_http() context manager for HTTP request capture.

Wraps ANY object that has callable HTTP methods (.get(), .post(), .request(), etc.).
Does NOT import any HTTP library or web framework.

Record mode: requests execute via underlying HTTP object, responses captured.
Replay mode: requests return recorded responses, underlying HTTP object not called.
Off mode: complete passthrough, zero overhead.

Fingerprint = normalize_url(url) + fingerprint(body) + fingerprint(stable_headers) + ordinal.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

from .context import SimContext, SimMode, get_context
from .canonical import fingerprint
from .fixture.schema import FixtureEvent
from .errors import SimStubMissError
from .trace import _make_serializable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class HTTPError(Exception):
    """Raised by FakeResponse.raise_for_status() for non-2xx status codes.

    Attributes:
        status_code: The HTTP status code.
        response: The FakeResponse that triggered the error.
    """

    def __init__(self, status_code: int, response: "FakeResponse"):
        self.status_code = status_code
        self.response = response
        super().__init__(f"HTTP {status_code}")


# ---------------------------------------------------------------------------
# FakeResponse — lightweight response for replay mode
# ---------------------------------------------------------------------------

class FakeResponse:
    """Lightweight HTTP response object for replay mode.

    Provides the same interface as common HTTP client responses without
    importing any HTTP library. Properties match the subset used by
    requests.Response, httpx.Response, and similar libraries.
    """

    def __init__(
        self,
        status_code: int = 200,
        body: str = "",
        headers: Optional[Dict[str, str]] = None,
    ):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    @property
    def text(self) -> str:
        return self._body

    @property
    def content(self) -> bytes:
        return self._body.encode("utf-8")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        return json.loads(self._body)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise HTTPError(self.status_code, self)


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """Normalize a URL for deterministic fingerprinting.

    - Lowercase scheme + host
    - Sort query params alphabetically
    - Strip fragments

    Uses only urllib.parse (stdlib).
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path
    # Sort query params alphabetically
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    sorted_params = sorted(query_params.items())
    # Flatten multi-value params deterministically
    flat_params = []
    for key, values in sorted_params:
        for val in sorted(values):
            flat_params.append((key, val))
    query = urlencode(flat_params)
    # Strip fragment
    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


# ---------------------------------------------------------------------------
# Stable headers extraction
# ---------------------------------------------------------------------------

_STABLE_HEADERS = {"content-type", "accept", "authorization"}
_EXCLUDED_HEADERS = {"user-agent", "x-request-id", "x-trace-id", "date"}


def _extract_stable_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Extract fingerprint-safe headers.

    Include: content-type, accept, authorization (hashed via SHA-256).
    Exclude: user-agent, x-request-id, x-trace-id, date.
    Hardcoded for V0.
    """
    if not headers:
        return {}

    stable = {}
    for key, value in headers.items():
        lower_key = key.lower()
        if lower_key in _EXCLUDED_HEADERS:
            continue
        if lower_key not in _STABLE_HEADERS:
            continue
        if lower_key == "authorization":
            # Hash authorization value for privacy
            stable[lower_key] = hashlib.sha256(value.encode("utf-8")).hexdigest()
        else:
            stable[lower_key] = value

    return stable


# ---------------------------------------------------------------------------
# HTTP fingerprinting
# ---------------------------------------------------------------------------

def _compute_http_fingerprint(
    method: str,
    url: str,
    body: Any,
    headers: Optional[Dict[str, str]],
) -> Tuple[str, str, str]:
    """Compute fingerprints for an HTTP request.

    Returns:
        Tuple of (method_url_fp, body_fp, headers_fp)
    """
    normalized = normalize_url(url)
    method_url_fp = fingerprint(f"{method.upper()}:{normalized}")

    if body is not None:
        body_data = _make_serializable(body)
        body_fp = fingerprint(body_data)
    else:
        body_fp = fingerprint("")

    stable = _extract_stable_headers(headers)
    if stable:
        headers_fp = fingerprint(stable)
    else:
        headers_fp = fingerprint("")

    return method_url_fp, body_fp, headers_fp


# ---------------------------------------------------------------------------
# Fixture I/O
# ---------------------------------------------------------------------------

def _http_fixture_key(
    name: str, method: str, url_fp: str, body_fp: str, ordinal: int,
) -> str:
    """Build the relative path key for an HTTP fixture file.

    Layout: __http__/{safe_name}_{METHOD}_{url_fp[:8]}_{body_fp[:8]}_{ordinal}.json
    """
    safe_name = name.replace(".", "_").replace("/", "_").replace(" ", "_")
    return (
        f"__http__/{safe_name}_{method.upper()}"
        f"_{url_fp[:8]}_{body_fp[:8]}_{ordinal}.json"
    )


def _write_http_fixture(
    name: str,
    method: str,
    url: str,
    body: Any,
    url_fp: str,
    body_fp: str,
    headers_fp: str,
    ordinal: int,
    response_data: Dict[str, Any],
    ctx: SimContext,
) -> None:
    """Persist an HTTP request fixture to sink or stub_dir."""
    key = _http_fixture_key(name, method, url_fp, body_fp, ordinal)

    if ctx.sink is not None:
        event = FixtureEvent(
            fixture_id=str(uuid.uuid4())[:8],
            qualname=f"http:{name}",
            run_id=ctx.run_id,
            recorded_at=datetime.now(timezone.utc).isoformat(),
            input={
                "method": method.upper(),
                "url": url,
                "body": _make_serializable(body),
            },
            input_fingerprint=f"{url_fp[:16]}:{body_fp[:16]}",
            output=response_data,
            ordinal=ordinal,
            storage_key=key,
            event_type="Stub",
        )
        ctx.sink.emit(event)
        return

    if ctx.stub_dir is not None:
        data = {
            "type": "http_request",
            "name": name,
            "method": method.upper(),
            "url": url,
            "body": _make_serializable(body),
            "url_fingerprint": url_fp,
            "body_fingerprint": body_fp,
            "headers_fingerprint": headers_fp,
            "ordinal": ordinal,
            "response": response_data,
        }
        filepath = ctx.stub_dir / key
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return

    logger.debug("No sink or stub_dir — http fixture %r discarded", name)


def _read_http_fixture(
    name: str,
    method: str,
    url_fp: str,
    body_fp: str,
    ordinal: int,
    stub_dir: Path,
) -> Optional[Dict[str, Any]]:
    """Read a recorded HTTP fixture from stub_dir."""
    key = _http_fixture_key(name, method, url_fp, body_fp, ordinal)
    filepath = stub_dir / key
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------------------
# Response extraction — duck-typed
# ---------------------------------------------------------------------------

def _extract_response(response: Any) -> Dict[str, Any]:
    """Extract response data from any HTTP client response object.

    Duck-typed: probes for common response attributes without importing
    any HTTP library.

    Returns:
        {"status_code": int, "body": str, "headers": dict}
    """
    # Status code: .status_code (requests/httpx) or .status (aiohttp/urllib3)
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        status_code = getattr(response, "status", 200)

    # Body: .text (str) or .data (bytes)
    body = getattr(response, "text", None)
    if body is None:
        data = getattr(response, "data", None)
        if isinstance(data, bytes):
            body = data.decode("utf-8", errors="replace")
        elif data is not None:
            body = str(data)
        else:
            body = ""

    # Headers: dict or dict-like
    raw_headers = getattr(response, "headers", {})
    if isinstance(raw_headers, dict):
        headers = {str(k): str(v) for k, v in raw_headers.items()}
    elif hasattr(raw_headers, "items"):
        headers = {str(k): str(v) for k, v in raw_headers.items()}
    else:
        headers = {}

    return {
        "status_code": int(status_code),
        "body": str(body),
        "headers": headers,
    }


# ---------------------------------------------------------------------------
# Call argument parsing
# ---------------------------------------------------------------------------

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "request"}


def _parse_call_args(
    method_name: str, args: tuple, kwargs: dict,
) -> Tuple[str, str, Any, Optional[Dict[str, str]]]:
    """Parse HTTP call arguments into (method, url, body, headers).

    Handles two calling patterns:
    - .request("GET", url, ...) — method from first positional arg
    - .get(url, ...) — method from method name

    Returns:
        (http_method, url, body, headers)
    """
    if method_name == "request":
        # .request("GET", url, ...) pattern
        http_method = args[0].upper() if len(args) > 0 else kwargs.get("method", "GET").upper()
        url = args[1] if len(args) > 1 else kwargs.get("url", "")
    else:
        # .get(url, ...) pattern — method from method name
        http_method = method_name.upper()
        url = args[0] if len(args) > 0 else kwargs.get("url", "")

    # Extract body from kwargs
    body = kwargs.get("json") or kwargs.get("data") or kwargs.get("body")

    # Extract headers from kwargs
    headers = kwargs.get("headers")

    return http_method, url, body, headers


# ---------------------------------------------------------------------------
# HTTPProxy — intercepts HTTP method calls
# ---------------------------------------------------------------------------

class HTTPProxy:
    """Transparent proxy that intercepts HTTP method calls on an HTTP client object.

    In record mode, calls the real method and captures the response.
    In replay mode, returns a FakeResponse without calling the real method.
    Delegates all other attribute access to the underlying object.
    """

    def __init__(self, http_object: Any, name: str, ctx: SimContext):
        # Use object.__setattr__ to avoid triggering our __setattr__ if defined
        object.__setattr__(self, "_http_object", http_object)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_ctx", ctx)

    def __getattr__(self, attr: str) -> Any:
        """Intercept HTTP methods; delegate everything else to the real object."""
        if attr in _HTTP_METHODS:
            return self._make_interceptor(attr)
        return getattr(object.__getattribute__(self, "_http_object"), attr)

    def _make_interceptor(self, method_name: str):
        """Return a closure that routes HTTP calls through _intercept_call."""
        def interceptor(*args: Any, **kwargs: Any) -> Any:
            return self._intercept_call(method_name, args, kwargs)
        return interceptor

    def _intercept_call(
        self, method_name: str, args: tuple, kwargs: dict,
    ) -> Any:
        """Route an HTTP call to replay, record, or passthrough based on mode."""
        ctx = object.__getattribute__(self, "_ctx")
        name = object.__getattribute__(self, "_name")
        http_object = object.__getattribute__(self, "_http_object")

        # Parse call arguments
        http_method, url, body, headers = _parse_call_args(method_name, args, kwargs)

        # Compute fingerprints
        url_fp, body_fp, headers_fp = _compute_http_fingerprint(
            http_method, url, body, headers,
        )
        combined_fp = f"http:{name}:{http_method}:{url_fp[:16]}:{body_fp[:16]}"
        ordinal = ctx.next_ordinal(combined_fp)

        if ctx.is_replaying:
            return self._replay_call(
                http_method, url, body, url_fp, body_fp, headers_fp,
                ordinal, name, ctx,
            )

        if ctx.is_recording:
            return self._record_call(
                method_name, http_method, url, body, url_fp, body_fp,
                headers_fp, ordinal, name, http_object, ctx, args, kwargs,
            )

        # Should not reach here (off mode is handled by sim_http yielding raw object)
        real_method = getattr(http_object, method_name)
        return real_method(*args, **kwargs)

    def _replay_call(
        self,
        http_method: str,
        url: str,
        body: Any,
        url_fp: str,
        body_fp: str,
        headers_fp: str,
        ordinal: int,
        name: str,
        ctx: SimContext,
    ) -> FakeResponse:
        """Handle an HTTP call in replay mode."""
        if ctx.stub_dir is None:
            raise SimStubMissError(
                "http", f"{url_fp[:16]}:{body_fp[:16]}", ordinal, [],
            )

        fixture = _read_http_fixture(
            name, http_method, url_fp, body_fp, ordinal, ctx.stub_dir,
        )
        if fixture is None:
            raise SimStubMissError(
                "http", f"{url_fp[:16]}:{body_fp[:16]}", ordinal, [],
            )

        response_data = fixture.get("response", {})
        fake_resp = FakeResponse(
            status_code=response_data.get("status_code", 200),
            body=response_data.get("body", ""),
            headers=response_data.get("headers", {}),
        )

        # Push to collected_stubs for outer @sim_trace
        ctx.collected_stubs.append({
            "type": "http_request",
            "name": name,
            "method": http_method,
            "url": url,
            "ordinal": ordinal,
            "response": response_data,
            "source": "replay",
        })

        return fake_resp

    def _record_call(
        self,
        method_name: str,
        http_method: str,
        url: str,
        body: Any,
        url_fp: str,
        body_fp: str,
        headers_fp: str,
        ordinal: int,
        name: str,
        http_object: Any,
        ctx: SimContext,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        """Handle an HTTP call in record mode."""
        # Execute the real request
        real_method = getattr(http_object, method_name)
        real_response = real_method(*args, **kwargs)

        # Extract response data
        response_data = _extract_response(real_response)

        # Write fixture
        _write_http_fixture(
            name, http_method, url, body, url_fp, body_fp, headers_fp,
            ordinal, response_data, ctx,
        )

        # Push to collected_stubs for outer @sim_trace
        ctx.collected_stubs.append({
            "type": "http_request",
            "name": name,
            "method": http_method,
            "url": url,
            "ordinal": ordinal,
            "response": response_data,
            "source": "record",
        })

        return real_response


# ---------------------------------------------------------------------------
# Public API — sim_http context manager
# ---------------------------------------------------------------------------

class sim_http:
    """Context manager that wraps an HTTP client object to intercept HTTP calls.

    Record mode::

        with sim_http(session, name="stripe") as s:
            resp = s.post("https://api.stripe.com/v1/charges", json={"amount": 100})

    Replay mode::

        with sim_http(session, name="stripe") as s:
            resp = s.post("https://api.stripe.com/v1/charges", json={"amount": 100})
            # returns FakeResponse, session.post() never called

    Off mode: yields the original http_object unwrapped.

    Args:
        http_object: Any object with HTTP methods (.get(), .post(), .request(), etc.).
        name: Label for this HTTP client (used in fixture file paths).
    """

    def __init__(self, http_object: Any, name: str = "http"):
        self._http_object = http_object
        self._name = name
        self._proxy: Optional[HTTPProxy] = None
        self._ctx: Optional[SimContext] = None

    def _setup(self) -> Any:
        """Common setup for both sync and async entry."""
        self._ctx = get_context()

        if not self._ctx.is_active:
            # Off mode — return original object unwrapped
            return self._http_object

        self._proxy = HTTPProxy(self._http_object, self._name, self._ctx)
        return self._proxy

    def _teardown(self) -> None:
        """Common teardown for both sync and async exit."""
        # Nothing to clean up — stubs are pushed per-request in HTTPProxy
        pass

    # -- Sync context manager -----------------------------------------------

    def __enter__(self) -> Any:
        return self._setup()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._teardown()

    # -- Async context manager ----------------------------------------------

    async def __aenter__(self) -> Any:
        return self._setup()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._teardown()
