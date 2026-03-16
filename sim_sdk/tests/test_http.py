"""
Tests for sim_http() HTTP Context Manager

Uses FakeHTTPClient / FakeClientResponse — NOT requests, httpx, or any real HTTP library.

Covers all acceptance criteria:
1.  Record mode — real HTTP method called, fixture written, stubs collected
2.  Replay mode — returns FakeResponse, real client NOT called, stubs pushed
3.  Replay stub miss — missing fixture -> SimStubMissError, different URL/body -> miss
4.  FakeResponse — .status_code, .json(), .text, .headers, .content, .ok, .raise_for_status()
5.  Ordinal tracking — same endpoint increments ordinal, replay respects ordinals
6.  Original unmodified — no attributes added, proxy is different object
7.  Generic interface — works with any object with .get()/.post()/.request()
8.  Zero dependencies — source inspection for forbidden imports
9.  Off mode — returns original object, no fixtures created
10. Round-trip — record then replay returns identical response data
11. URL normalization — query param sorting, scheme lowercasing, fragment stripping
12. Stable headers — authorization hashed, content-type included, user-agent excluded
13. Sink integration — ctx.sink.emit() called with correct FixtureEvent
14. Async context manager — async with sim_http(...) works
15. Call arg parsing — .request("GET", url) vs .get(url) both work
"""

import asyncio
import inspect
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from sim_sdk.context import SimContext, SimMode, set_context, clear_context
from sim_sdk.http import (
    sim_http,
    HTTPProxy,
    FakeResponse,
    HTTPError,
    normalize_url,
    _extract_stable_headers,
    _parse_call_args,
    _compute_http_fingerprint,
)
from sim_sdk.errors import SimStubMissError


# ---------------------------------------------------------------------------
# FakeHTTPClient — test double with .get(), .post(), .request() methods
# ---------------------------------------------------------------------------

class FakeClientResponse:
    """Mimics a real HTTP response object (duck-typed)."""

    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeHTTPClient:
    """A fake HTTP client with .get(), .post(), .put(), .delete(), .request() methods.

    Tracks all calls so tests can verify what was actually called.
    """

    def __init__(self):
        self.call_log: list = []
        self._responses: dict = {}  # (method, url) -> FakeClientResponse

    def set_response(self, method: str, url: str, response: FakeClientResponse):
        """Pre-configure what a method should return for a given URL."""
        self._responses[(method.upper(), url)] = response

    def _handle(self, method: str, url: str, **kwargs):
        self.call_log.append({"method": method, "url": url, "kwargs": kwargs})
        return self._responses.get(
            (method.upper(), url),
            FakeClientResponse(200, '{"ok": true}'),
        )

    def get(self, url, **kwargs):
        return self._handle("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._handle("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._handle("PUT", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._handle("DELETE", url, **kwargs)

    def patch(self, url, **kwargs):
        return self._handle("PATCH", url, **kwargs)

    def head(self, url, **kwargs):
        return self._handle("HEAD", url, **kwargs)

    def options(self, url, **kwargs):
        return self._handle("OPTIONS", url, **kwargs)

    def request(self, method, url, **kwargs):
        return self._handle(method.upper(), url, **kwargs)

    def some_other_method(self):
        """A non-HTTP method that should be delegated through the proxy."""
        return "other_result"


class FakeHTTPGetOnly:
    """A fake HTTP client with only .get() — no other HTTP methods."""

    def __init__(self):
        self.call_log: list = []

    def get(self, url, **kwargs):
        self.call_log.append({"method": "GET", "url": url})
        return FakeClientResponse(200, '{"data": "hello"}')


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_context():
    """Ensure each test starts with a clean context."""
    clear_context()
    yield
    clear_context()


@pytest.fixture
def stub_dir(tmp_path):
    """Provide a temporary stub directory."""
    return tmp_path / "stubs"


def make_record_ctx(stub_dir: Path, run_id: str = "test-run") -> SimContext:
    """Create a record-mode context."""
    stub_dir.mkdir(parents=True, exist_ok=True)
    ctx = SimContext(mode=SimMode.RECORD, run_id=run_id, stub_dir=stub_dir)
    set_context(ctx)
    return ctx


def make_replay_ctx(stub_dir: Path, run_id: str = "test-run") -> SimContext:
    """Create a replay-mode context."""
    ctx = SimContext(mode=SimMode.REPLAY, run_id=run_id, stub_dir=stub_dir)
    set_context(ctx)
    return ctx


def make_off_ctx() -> SimContext:
    """Create an off-mode context."""
    ctx = SimContext(mode=SimMode.OFF)
    set_context(ctx)
    return ctx


# ===========================================================================
# 1. Record Mode
# ===========================================================================

class TestRecordMode:
    """Record mode: real HTTP method called, fixture written, stubs collected."""

    def test_get_executes_on_real_client(self, stub_dir):
        """In record mode, .get() calls the real HTTP client."""
        make_record_ctx(stub_dir)
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/users",
                            FakeClientResponse(200, '[{"id": 1}]'))

        with sim_http(client, name="api") as s:
            result = s.get("https://api.example.com/users")

        assert result.status_code == 200
        assert len(client.call_log) == 1
        assert client.call_log[0]["url"] == "https://api.example.com/users"

    def test_post_executes_on_real_client(self, stub_dir):
        """In record mode, .post() calls the real HTTP client."""
        make_record_ctx(stub_dir)
        client = FakeHTTPClient()
        client.set_response("POST", "https://api.example.com/users",
                            FakeClientResponse(201, '{"id": 42}'))

        with sim_http(client, name="api") as s:
            result = s.post("https://api.example.com/users", json={"name": "Alice"})

        assert result.status_code == 201
        assert len(client.call_log) == 1

    def test_fixture_written_to_disk(self, stub_dir):
        """Record mode writes a fixture JSON under __http__/."""
        make_record_ctx(stub_dir)
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/data",
                            FakeClientResponse(200, '{"key": "value"}',
                                               {"Content-Type": "application/json"}))

        with sim_http(client, name="myapi") as s:
            s.get("https://api.example.com/data")

        http_dir = stub_dir / "__http__"
        assert http_dir.exists()
        files = list(http_dir.iterdir())
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["type"] == "http_request"
        assert data["name"] == "myapi"
        assert data["method"] == "GET"
        assert data["url"] == "https://api.example.com/data"
        assert data["response"]["status_code"] == 200
        assert data["response"]["body"] == '{"key": "value"}'

    def test_stubs_collected(self, stub_dir):
        """Record mode pushes request to ctx.collected_stubs."""
        ctx = make_record_ctx(stub_dir)
        client = FakeHTTPClient()

        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/data")

        assert len(ctx.collected_stubs) == 1
        stub = ctx.collected_stubs[0]
        assert stub["type"] == "http_request"
        assert stub["name"] == "api"
        assert stub["source"] == "record"
        assert stub["method"] == "GET"


# ===========================================================================
# 2. Replay Mode
# ===========================================================================

class TestReplayMode:
    """Replay mode: returns FakeResponse, real client NOT called, stubs pushed."""

    def test_replay_returns_fake_response(self, stub_dir):
        """Replay returns a FakeResponse with the recorded data."""
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/users",
                            FakeClientResponse(200, '[{"id": 1}]',
                                               {"Content-Type": "application/json"}))

        # Record
        make_record_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/users")

        client.call_log.clear()

        # Replay
        make_replay_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            result = s.get("https://api.example.com/users")

        assert isinstance(result, FakeResponse)
        assert result.status_code == 200
        assert result.text == '[{"id": 1}]'

    def test_replay_does_not_call_real_client(self, stub_dir):
        """In replay mode, the real HTTP client is never called."""
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/users",
                            FakeClientResponse(200, '{"ok": true}'))

        # Record
        make_record_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/users")

        client.call_log.clear()

        # Replay
        make_replay_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/users")

        assert len(client.call_log) == 0

    def test_replay_pushes_stub(self, stub_dir):
        """Replay pushes recorded response to ctx.collected_stubs."""
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/data",
                            FakeClientResponse(200, '{"v": 1}'))

        make_record_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/data")

        ctx = make_replay_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/data")

        assert len(ctx.collected_stubs) == 1
        assert ctx.collected_stubs[0]["source"] == "replay"


# ===========================================================================
# 3. Replay Stub Miss
# ===========================================================================

class TestReplayStubMiss:
    """Missing fixture -> SimStubMissError, different URL/body -> miss."""

    def test_missing_fixture_raises(self, stub_dir):
        """Replay with no recorded fixture raises SimStubMissError."""
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)
        client = FakeHTTPClient()

        with pytest.raises(SimStubMissError):
            with sim_http(client, name="api") as s:
                s.get("https://api.example.com/nonexistent")

    def test_error_has_diagnostics(self, stub_dir):
        """SimStubMissError contains the http name in qualname."""
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)
        client = FakeHTTPClient()

        with pytest.raises(SimStubMissError) as exc_info:
            with sim_http(client, name="myapi") as s:
                s.get("https://api.example.com/missing")

        assert exc_info.value.stub_type == "http"

    def test_missing_stub_dir_raises(self):
        """Replay with no stub_dir raises SimStubMissError."""
        ctx = SimContext(mode=SimMode.REPLAY, run_id="test", stub_dir=None)
        set_context(ctx)
        client = FakeHTTPClient()

        with pytest.raises(SimStubMissError):
            with sim_http(client) as s:
                s.get("https://api.example.com/data")

    def test_different_url_miss(self, stub_dir):
        """Same method with different URL produces a miss."""
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/a",
                            FakeClientResponse(200, "a"))

        # Record GET /a
        make_record_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/a")

        # Replay GET /b — should miss
        make_replay_ctx(stub_dir)
        with pytest.raises(SimStubMissError):
            with sim_http(client, name="api") as s:
                s.get("https://api.example.com/b")

    def test_different_body_miss(self, stub_dir):
        """Same URL with different body produces a miss."""
        client = FakeHTTPClient()
        client.set_response("POST", "https://api.example.com/data",
                            FakeClientResponse(200, "ok"))

        # Record with body={"a": 1}
        make_record_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.post("https://api.example.com/data", json={"a": 1})

        # Replay with body={"a": 999} — should miss
        make_replay_ctx(stub_dir)
        with pytest.raises(SimStubMissError):
            with sim_http(client, name="api") as s:
                s.post("https://api.example.com/data", json={"a": 999})


# ===========================================================================
# 4. FakeResponse
# ===========================================================================

class TestFakeResponse:
    """.status_code, .json(), .text, .headers, .content, .ok, .raise_for_status()"""

    def test_status_code(self):
        r = FakeResponse(status_code=201)
        assert r.status_code == 201

    def test_text(self):
        r = FakeResponse(body="hello world")
        assert r.text == "hello world"

    def test_content(self):
        r = FakeResponse(body="hello")
        assert r.content == b"hello"

    def test_json(self):
        r = FakeResponse(body='{"key": "value"}')
        assert r.json() == {"key": "value"}

    def test_headers(self):
        r = FakeResponse(headers={"Content-Type": "application/json"})
        assert r.headers["Content-Type"] == "application/json"

    def test_ok_true(self):
        for code in [200, 201, 204, 299]:
            r = FakeResponse(status_code=code)
            assert r.ok is True

    def test_ok_false(self):
        for code in [400, 404, 500, 199]:
            r = FakeResponse(status_code=code)
            assert r.ok is False

    def test_raise_for_status_ok(self):
        """No exception for 2xx status codes."""
        r = FakeResponse(status_code=200)
        r.raise_for_status()  # Should not raise

    def test_raise_for_status_error(self):
        """HTTPError raised for non-2xx status codes."""
        r = FakeResponse(status_code=404)
        with pytest.raises(HTTPError) as exc_info:
            r.raise_for_status()
        assert exc_info.value.status_code == 404
        assert exc_info.value.response is r

    def test_default_values(self):
        """FakeResponse has sensible defaults."""
        r = FakeResponse()
        assert r.status_code == 200
        assert r.text == ""
        assert r.headers == {}
        assert r.ok is True


# ===========================================================================
# 5. Ordinal Tracking
# ===========================================================================

class TestOrdinalTracking:
    """Same endpoint increments ordinal, replay respects ordinals."""

    def test_same_endpoint_increments_ordinal(self, stub_dir):
        """Two identical requests get ordinal 0 and 1."""
        make_record_ctx(stub_dir)
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/data",
                            FakeClientResponse(200, '{"v": 1}'))

        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/data")
            s.get("https://api.example.com/data")

        http_dir = stub_dir / "__http__"
        files = sorted(http_dir.iterdir())
        assert len(files) == 2

        data0 = json.loads(files[0].read_text())
        data1 = json.loads(files[1].read_text())
        assert data0["ordinal"] == 0
        assert data1["ordinal"] == 1

    def test_replay_respects_ordinals(self, stub_dir):
        """Replay returns correct response for each ordinal."""
        client = FakeHTTPClient()

        # Record — first call returns v1, second returns v2
        make_record_ctx(stub_dir)
        client.set_response("GET", "https://api.example.com/data",
                            FakeClientResponse(200, '{"v": 1}'))
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/data")

        # Change response for second call
        client.set_response("GET", "https://api.example.com/data",
                            FakeClientResponse(200, '{"v": 2}'))
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/data")

        client.call_log.clear()

        # Replay — first ordinal
        make_replay_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            r1 = s.get("https://api.example.com/data")

        assert r1.json() == {"v": 1}


# ===========================================================================
# 6. Original Object Unmodified
# ===========================================================================

class TestOriginalUnmodified:
    """No attributes added to original, proxy is different object."""

    def test_no_attributes_added(self, stub_dir):
        """The original HTTP client has no new attributes after sim_http."""
        make_record_ctx(stub_dir)
        client = FakeHTTPClient()
        attrs_before = set(dir(client))

        with sim_http(client) as s:
            s.get("https://api.example.com/data")

        attrs_after = set(dir(client))
        assert attrs_before == attrs_after

    def test_proxy_is_different_object(self, stub_dir):
        """The yielded proxy is NOT the original object."""
        make_record_ctx(stub_dir)
        client = FakeHTTPClient()

        with sim_http(client) as s:
            assert s is not client
            assert isinstance(s, HTTPProxy)


# ===========================================================================
# 7. Generic Interface
# ===========================================================================

class TestGenericInterface:
    """Works with any object with .get()/.post()/.request(), other methods delegated."""

    def test_get_only_object(self, stub_dir):
        """Works with an object that only has .get()."""
        make_record_ctx(stub_dir)
        client = FakeHTTPGetOnly()

        with sim_http(client, name="getonly") as s:
            result = s.get("https://api.example.com/data")

        assert result.status_code == 200
        assert len(client.call_log) == 1

    def test_request_method_works(self, stub_dir):
        """Works with .request("GET", url) pattern."""
        make_record_ctx(stub_dir)
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/data",
                            FakeClientResponse(200, '{"via": "request"}'))

        with sim_http(client, name="api") as s:
            result = s.request("GET", "https://api.example.com/data")

        assert result.status_code == 200
        assert len(client.call_log) == 1

    def test_other_methods_delegated(self, stub_dir):
        """Non-HTTP methods are delegated to the underlying object."""
        make_record_ctx(stub_dir)
        client = FakeHTTPClient()

        with sim_http(client) as s:
            result = s.some_other_method()

        assert result == "other_result"


# ===========================================================================
# 8. Zero Framework Dependencies
# ===========================================================================

class TestZeroDependencies:
    """http.py must not import any HTTP library or web framework."""

    def test_no_forbidden_imports(self):
        """Verify http.py source has no forbidden imports."""
        source = inspect.getsource(__import__("sim_sdk.http", fromlist=["http"]))

        forbidden = [
            "requests", "httpx", "aiohttp", "urllib3",
            "flask", "django", "fastapi", "starlette",
            "tornado", "bottle", "sanic",
            "psycopg2", "sqlalchemy", "pymongo",
        ]
        for lib in forbidden:
            assert f"import {lib}" not in source, (
                f"http.py imports forbidden library: {lib}"
            )
            assert f"from {lib}" not in source, (
                f"http.py imports forbidden library: {lib}"
            )


# ===========================================================================
# 9. Off Mode
# ===========================================================================

class TestOffMode:
    """Returns original object, no fixtures created."""

    def test_off_mode_returns_original_object(self):
        """In off mode, sim_http yields the original HTTP object unwrapped."""
        make_off_ctx()
        client = FakeHTTPClient()

        with sim_http(client) as s:
            assert s is client  # Same object, not a proxy

    def test_off_mode_request_works(self):
        """Requests work normally in off mode."""
        make_off_ctx()
        client = FakeHTTPClient()

        with sim_http(client) as s:
            result = s.get("https://api.example.com/data")

        assert result.status_code == 200
        assert len(client.call_log) == 1

    def test_off_mode_no_fixtures_created(self, stub_dir):
        """No fixture files are created in off mode."""
        make_off_ctx()
        client = FakeHTTPClient()

        with sim_http(client) as s:
            s.get("https://api.example.com/data")

        assert not stub_dir.exists()


# ===========================================================================
# 10. Round-Trip
# ===========================================================================

class TestRoundtrip:
    """Record then replay returns identical response data."""

    def test_roundtrip_get(self, stub_dir):
        """GET response survives record/replay round-trip."""
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/users",
                            FakeClientResponse(200, '[{"id": 1, "name": "Alice"}]',
                                               {"Content-Type": "application/json"}))

        # Record
        make_record_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/users")

        client.call_log.clear()

        # Replay
        make_replay_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            result = s.get("https://api.example.com/users")

        assert result.status_code == 200
        assert result.json() == [{"id": 1, "name": "Alice"}]
        assert len(client.call_log) == 0

    def test_roundtrip_post_with_body(self, stub_dir):
        """POST with body round-trips correctly."""
        client = FakeHTTPClient()
        client.set_response("POST", "https://api.example.com/users",
                            FakeClientResponse(201, '{"id": 42}'))

        make_record_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.post("https://api.example.com/users", json={"name": "Alice"})

        client.call_log.clear()

        make_replay_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            result = s.post("https://api.example.com/users", json={"name": "Alice"})

        assert result.status_code == 201
        assert result.json() == {"id": 42}

    def test_roundtrip_error_status(self, stub_dir):
        """Error status code round-trips correctly."""
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/missing",
                            FakeClientResponse(404, '{"error": "not found"}'))

        make_record_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/missing")

        make_replay_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            result = s.get("https://api.example.com/missing")

        assert result.status_code == 404
        assert result.ok is False


# ===========================================================================
# 11. URL Normalization
# ===========================================================================

class TestURLNormalization:
    """Query param sorting, scheme lowercasing, fragment stripping."""

    def test_query_param_sorting(self):
        """Query params are sorted alphabetically."""
        url1 = normalize_url("https://api.example.com/data?b=2&a=1")
        url2 = normalize_url("https://api.example.com/data?a=1&b=2")
        assert url1 == url2

    def test_scheme_lowercasing(self):
        """Scheme is lowercased."""
        url = normalize_url("HTTPS://API.EXAMPLE.COM/data")
        assert url.startswith("https://api.example.com")

    def test_fragment_stripping(self):
        """Fragments are stripped."""
        url = normalize_url("https://api.example.com/data#section")
        assert "#" not in url

    def test_host_lowercasing(self):
        """Host is lowercased."""
        url = normalize_url("https://API.Example.COM/Data")
        assert "api.example.com" in url

    def test_preserves_path(self):
        """Path is preserved as-is (case-sensitive)."""
        url = normalize_url("https://api.example.com/Data/Items")
        assert "/Data/Items" in url

    def test_same_url_same_fingerprint(self, stub_dir):
        """URLs that normalize to the same string produce matching fixtures."""
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/data?b=2&a=1",
                            FakeClientResponse(200, '{"ok": true}'))
        client.set_response("GET", "https://api.example.com/data?a=1&b=2",
                            FakeClientResponse(200, '{"ok": true}'))

        # Record with ?b=2&a=1
        make_record_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.get("https://api.example.com/data?b=2&a=1")

        client.call_log.clear()

        # Replay with ?a=1&b=2 — should match because URL normalizes the same
        make_replay_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            result = s.get("https://api.example.com/data?a=1&b=2")

        assert result.status_code == 200


# ===========================================================================
# 12. Stable Headers
# ===========================================================================

class TestStableHeaders:
    """Authorization hashed, Content-Type included, User-Agent excluded."""

    def test_authorization_hashed(self):
        """Authorization header value is hashed, not stored in plaintext."""
        headers = {"Authorization": "Bearer secret-token-123"}
        stable = _extract_stable_headers(headers)
        assert "authorization" in stable
        assert stable["authorization"] != "Bearer secret-token-123"
        # Verify it's a SHA-256 hash (64 hex chars)
        assert len(stable["authorization"]) == 64

    def test_content_type_included(self):
        """Content-Type is included as-is."""
        headers = {"Content-Type": "application/json"}
        stable = _extract_stable_headers(headers)
        assert stable["content-type"] == "application/json"

    def test_accept_included(self):
        """Accept is included as-is."""
        headers = {"Accept": "text/html"}
        stable = _extract_stable_headers(headers)
        assert stable["accept"] == "text/html"

    def test_user_agent_excluded(self):
        """User-Agent is excluded from stable headers."""
        headers = {"User-Agent": "test-agent/1.0", "Content-Type": "text/plain"}
        stable = _extract_stable_headers(headers)
        assert "user-agent" not in stable

    def test_request_id_excluded(self):
        """X-Request-Id is excluded."""
        headers = {"X-Request-Id": "abc-123"}
        stable = _extract_stable_headers(headers)
        assert "x-request-id" not in stable

    def test_trace_id_excluded(self):
        """X-Trace-Id is excluded."""
        headers = {"X-Trace-Id": "trace-456"}
        stable = _extract_stable_headers(headers)
        assert "x-trace-id" not in stable

    def test_date_excluded(self):
        """Date header is excluded."""
        headers = {"Date": "Thu, 01 Jan 2025 00:00:00 GMT"}
        stable = _extract_stable_headers(headers)
        assert "date" not in stable

    def test_empty_headers(self):
        """Empty or None headers return empty dict."""
        assert _extract_stable_headers(None) == {}
        assert _extract_stable_headers({}) == {}

    def test_same_auth_same_hash(self):
        """Same authorization value produces same hash."""
        h1 = _extract_stable_headers({"Authorization": "Bearer abc"})
        h2 = _extract_stable_headers({"Authorization": "Bearer abc"})
        assert h1["authorization"] == h2["authorization"]

    def test_different_auth_different_hash(self):
        """Different authorization values produce different hashes."""
        h1 = _extract_stable_headers({"Authorization": "Bearer abc"})
        h2 = _extract_stable_headers({"Authorization": "Bearer xyz"})
        assert h1["authorization"] != h2["authorization"]


# ===========================================================================
# 13. Sink Integration
# ===========================================================================

class TestSinkIntegration:
    """ctx.sink.emit() called with correct FixtureEvent."""

    def test_record_emits_to_sink(self, stub_dir):
        """When ctx.sink is set, HTTP fixtures are emitted as FixtureEvents."""
        stub_dir.mkdir(parents=True, exist_ok=True)
        mock_sink = MagicMock()
        ctx = SimContext(
            mode=SimMode.RECORD, run_id="test", stub_dir=stub_dir, sink=mock_sink,
        )
        set_context(ctx)

        client = FakeHTTPClient()
        client.set_response("POST", "https://api.stripe.com/v1/charges",
                            FakeClientResponse(200, '{"id": "ch_1234"}'))

        with sim_http(client, name="stripe") as s:
            s.post("https://api.stripe.com/v1/charges", json={"amount": 100})

        mock_sink.emit.assert_called_once()
        event = mock_sink.emit.call_args[0][0]
        assert event.storage_key.startswith("__http__/stripe_POST_")
        assert event.qualname == "http:stripe"
        assert event.output["status_code"] == 200
        assert event.input["method"] == "POST"
        assert event.input["url"] == "https://api.stripe.com/v1/charges"


# ===========================================================================
# 14. Async Context Manager
# ===========================================================================

class TestAsyncContextManager:
    """async with sim_http(...) works."""

    def test_async_record(self, stub_dir):
        """Async record mode works."""
        async def _run():
            make_record_ctx(stub_dir)
            client = FakeHTTPClient()
            client.set_response("GET", "https://api.example.com/data",
                                FakeClientResponse(200, '{"async": true}'))

            async with sim_http(client, name="api") as s:
                result = s.get("https://api.example.com/data")

            assert result.status_code == 200

        asyncio.run(_run())

    def test_async_off_mode(self):
        """Async off mode returns original object."""
        async def _run():
            make_off_ctx()
            client = FakeHTTPClient()

            async with sim_http(client) as s:
                assert s is client

        asyncio.run(_run())


# ===========================================================================
# 15. Call Arg Parsing
# ===========================================================================

class TestCallArgParsing:
    """.request("GET", url) vs .get(url) both work."""

    def test_request_method_pattern(self):
        """Parses .request('GET', url, ...) correctly."""
        method, url, body, headers = _parse_call_args(
            "request", ("GET", "https://example.com"), {},
        )
        assert method == "GET"
        assert url == "https://example.com"
        assert body is None
        assert headers is None

    def test_get_method_pattern(self):
        """Parses .get(url, ...) correctly."""
        method, url, body, headers = _parse_call_args(
            "get", ("https://example.com",), {},
        )
        assert method == "GET"
        assert url == "https://example.com"

    def test_post_with_json_body(self):
        """Parses .post(url, json={...}) correctly."""
        method, url, body, headers = _parse_call_args(
            "post", ("https://example.com",), {"json": {"key": "val"}},
        )
        assert method == "POST"
        assert body == {"key": "val"}

    def test_post_with_data_body(self):
        """Parses .post(url, data=...) correctly."""
        method, url, body, headers = _parse_call_args(
            "post", ("https://example.com",), {"data": "raw-data"},
        )
        assert method == "POST"
        assert body == "raw-data"

    def test_headers_extracted(self):
        """Headers are extracted from kwargs."""
        method, url, body, headers = _parse_call_args(
            "get", ("https://example.com",),
            {"headers": {"Authorization": "Bearer abc"}},
        )
        assert headers == {"Authorization": "Bearer abc"}

    def test_request_with_kwargs(self):
        """Parses .request(method=..., url=...) from kwargs."""
        method, url, body, headers = _parse_call_args(
            "request", (), {"method": "PUT", "url": "https://example.com", "json": {"x": 1}},
        )
        assert method == "PUT"
        assert url == "https://example.com"
        assert body == {"x": 1}

    def test_roundtrip_request_vs_get(self, stub_dir):
        """Both .request('GET', url) and .get(url) produce the same fixture fingerprint."""
        client = FakeHTTPClient()
        client.set_response("GET", "https://api.example.com/data",
                            FakeClientResponse(200, '{"ok": true}'))

        # Record via .request("GET", url)
        make_record_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            s.request("GET", "https://api.example.com/data")

        client.call_log.clear()

        # Replay via .get(url) — should match the same fixture
        make_replay_ctx(stub_dir)
        with sim_http(client, name="api") as s:
            result = s.get("https://api.example.com/data")

        assert result.status_code == 200
        assert result.json() == {"ok": True}
