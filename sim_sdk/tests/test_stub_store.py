"""
Tests for StubStore — the in-memory replay data layer.

All tests against real recorded data load from the canonical fixture file:
  tests/fixtures/calculate_quote.json  (schema_version=1, FixtureEvent format)
"""

import json
from pathlib import Path

import pytest

from sim_sdk.stub_store import StubStore
from sim_sdk.errors import SimStubMissError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "calculate_quote.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_fixture(tmp_path, name: str, data: dict) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Main suite — canonical calculate_quote fixture
# ---------------------------------------------------------------------------

class TestStubStoreCalculateQuote:
    """Validate StubStore against the committed calculate_quote.json fixture."""

    @pytest.fixture()
    def store(self) -> StubStore:
        return StubStore.from_fixture(str(FIXTURE_PATH))

    # -- DB stubs -----------------------------------------------------------

    def test_products_returns_correct_rows(self, store):
        rows = store.get_db_stub("2c2c6f4f7f54aad7:fd5c81b9314f2dba", 0)
        assert rows == [
            {"sku": "GADGET-B", "name": "Super Gadget", "price": 49.99},
            {"sku": "WIDGET-A", "name": "Premium Widget", "price": 29.99},
        ]

    def test_users_returns_correct_rows(self, store):
        rows = store.get_db_stub("8e2da4922ec2cc96:080a9ed428559ef6", 0)
        assert rows == [{"region": "US-CA"}]

    def test_quotes_returns_write_result(self, store):
        result = store.get_db_stub("023d3a8283aba4ca:c7317460ab436af4", 0)
        assert result == {"rowcount": 1, "lastrowid": 1}

    def test_db_miss_wrong_fingerprint_raises(self, store):
        with pytest.raises(SimStubMissError) as exc_info:
            store.get_db_stub("deadbeefdeadbeef:0000000000000000", 0)
        err = exc_info.value
        assert err.stub_type == "db"
        assert err.fingerprint == "deadbeefdeadbeef:0000000000000000"
        assert err.ordinal == 0
        assert "2c2c6f4f7f54aad7:fd5c81b9314f2dba" in err.available
        assert "8e2da4922ec2cc96:080a9ed428559ef6" in err.available
        assert "023d3a8283aba4ca:c7317460ab436af4" in err.available

    def test_db_miss_wrong_ordinal_raises(self, store):
        with pytest.raises(SimStubMissError) as exc_info:
            store.get_db_stub("2c2c6f4f7f54aad7:fd5c81b9314f2dba", 1)
        err = exc_info.value
        assert err.stub_type == "db"
        assert err.ordinal == 1

    # -- HTTP / capture stubs -----------------------------------------------

    def test_tax_service_returns_capture_result(self, store):
        status, body, headers = store.get_http_stub("tax_service", 0)
        assert status == 200
        assert body == {"region": "US-CA", "rate": 0.0725}
        assert headers == {}

    def test_http_miss_raises(self, store):
        with pytest.raises(SimStubMissError) as exc_info:
            store.get_http_stub("nonexistent_service", 0)
        err = exc_info.value
        assert err.stub_type == "http"
        assert err.fingerprint == "nonexistent_service"
        assert "tax_service" in err.available

    # -- Trace stubs --------------------------------------------------------

    def test_trace_miss_fixture_has_no_trace_stubs(self, store):
        """Fixture contains only db: and capture: stubs — trace index is empty."""
        with pytest.raises(SimStubMissError) as exc_info:
            store.get_trace_stub("any_fp", 0)
        err = exc_info.value
        assert err.stub_type == "trace"
        assert err.available == []

    # -- golden_output is NOT indexed ---------------------------------------

    def test_golden_output_not_indexed_as_stub(self, store):
        """golden_output has event_type=Output; StubStore must not index it."""
        golden_fp = "77646bc6050bcb8b1021261c093e131af2238521d4a4c975aaad0a979970837c"
        with pytest.raises(SimStubMissError):
            store.get_trace_stub(golden_fp, 0)


# ---------------------------------------------------------------------------
# Ordinal tracking — same fingerprint called multiple times
# ---------------------------------------------------------------------------

class TestStubStoreOrdinalTracking:
    """Repeated calls with the same fingerprint are distinguished by ordinal."""

    @pytest.fixture()
    def store(self, tmp_path) -> StubStore:
        data = {
            "schema_version": 1,
            "stubs": [
                {
                    "qualname": "db:products",
                    "input_fingerprint": "aabbccdd:11223344",
                    "output": [{"row": 0}],
                    "ordinal": 0,
                    "event_type": "Stub",
                },
                {
                    "qualname": "db:products",
                    "input_fingerprint": "aabbccdd:11223344",
                    "output": [{"row": 1}],
                    "ordinal": 1,
                    "event_type": "Stub",
                },
            ],
        }
        return StubStore.from_fixture(_write_fixture(tmp_path, "ordinals.json", data))

    def test_ordinal_0_returns_first_result(self, store):
        assert store.get_db_stub("aabbccdd:11223344", 0) == [{"row": 0}]

    def test_ordinal_1_returns_second_result(self, store):
        assert store.get_db_stub("aabbccdd:11223344", 1) == [{"row": 1}]

    def test_ordinal_2_raises(self, store):
        with pytest.raises(SimStubMissError) as exc_info:
            store.get_db_stub("aabbccdd:11223344", 2)
        assert exc_info.value.ordinal == 2


# ---------------------------------------------------------------------------
# from_fixture error handling
# ---------------------------------------------------------------------------

class TestStubStoreFromFixtureErrors:

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Fixture not found"):
            StubStore.from_fixture(str(tmp_path / "does_not_exist.json"))

    def test_invalid_json_raises(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{{", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid JSON"):
            StubStore.from_fixture(str(bad))

    def test_empty_stubs_array_loads_cleanly(self, tmp_path):
        path = _write_fixture(tmp_path, "empty.json", {"schema_version": 1, "stubs": []})
        store = StubStore.from_fixture(path)
        with pytest.raises(SimStubMissError):
            store.get_db_stub("any", 0)

    def test_missing_stubs_key_loads_cleanly(self, tmp_path):
        path = _write_fixture(tmp_path, "no_stubs.json", {"schema_version": 1, "fixture_id": "abc"})
        store = StubStore.from_fixture(path)
        with pytest.raises(SimStubMissError):
            store.get_http_stub("any", 0)
