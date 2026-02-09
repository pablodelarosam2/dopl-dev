"""Tests for sim_sdk.store module."""

import pytest

from sim_sdk.store import StubStore


class TestStubStoreHttp:
    """Tests for HTTP stub storage."""

    def test_save_and_load_http(self, stub_store):
        response = {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": {"result": "success"},
        }

        stub_store.save_http("fp123", response)
        loaded = stub_store.load_http("fp123")

        assert loaded == response

    def test_load_nonexistent_http(self, stub_store):
        result = stub_store.load_http("nonexistent")
        assert result is None

    def test_has_http(self, stub_store):
        assert not stub_store.has_http("fp123")

        stub_store.save_http("fp123", {"status_code": 200})
        assert stub_store.has_http("fp123")

    def test_save_http_with_metadata(self, stub_store):
        response = {"status_code": 200}
        metadata = {"url": "https://api.example.com", "method": "GET"}

        path = stub_store.save_http("fp123", response, metadata=metadata)
        assert path.exists()


class TestStubStoreDb:
    """Tests for database stub storage."""

    def test_save_and_load_db(self, stub_store):
        rows = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]

        stub_store.save_db("fp456", 0, rows)
        loaded = stub_store.load_db("fp456", 0)

        assert loaded == rows

    def test_load_nonexistent_db(self, stub_store):
        result = stub_store.load_db("nonexistent", 0)
        assert result is None

    def test_ordinal_distinction(self, stub_store):
        rows1 = [{"id": 1}]
        rows2 = [{"id": 2}]

        stub_store.save_db("fp456", 0, rows1)
        stub_store.save_db("fp456", 1, rows2)

        assert stub_store.load_db("fp456", 0) == rows1
        assert stub_store.load_db("fp456", 1) == rows2

    def test_has_db(self, stub_store):
        assert not stub_store.has_db("fp456", 0)

        stub_store.save_db("fp456", 0, [])
        assert stub_store.has_db("fp456", 0)
        assert not stub_store.has_db("fp456", 1)


class TestStubStoreRequests:
    """Tests for request capture storage."""

    def test_save_and_load_request(self, stub_store):
        data = {
            "request_id": "req123",
            "request": {"method": "POST", "path": "/api"},
            "response": {"status_code": 200},
        }

        stub_store.save_request("req123", data)
        loaded = stub_store.load_request("req123")

        assert loaded == data

    def test_load_nonexistent_request(self, stub_store):
        result = stub_store.load_request("nonexistent")
        assert result is None

    def test_list_requests(self, stub_store):
        stub_store.save_request("req1", {"id": 1})
        stub_store.save_request("req2", {"id": 2})
        stub_store.save_request("req3", {"id": 3})

        requests = stub_store.list_requests()

        assert len(requests) == 3
        assert "req1" in requests
        assert "req2" in requests
        assert "req3" in requests


class TestStubStoreFixtures:
    """Tests for fixture storage."""

    def test_save_and_load_fixture(self, stub_store):
        request = {"method": "POST", "path": "/quote", "body": {"items": []}}
        db_stubs = [{"fingerprint": "db1", "ordinal": 0, "rows": []}]
        http_stubs = [{"fingerprint": "http1", "response": {"status_code": 200}}]

        stub_store.save_fixture("test_fixture", request, db_stubs, http_stubs)
        loaded = stub_store.load_fixture("test_fixture")

        assert loaded["name"] == "test_fixture"
        assert loaded["request"] == request
        assert loaded["db_stubs"] == db_stubs
        assert loaded["http_stubs"] == http_stubs

    def test_list_fixtures(self, stub_store):
        stub_store.save_fixture("fixture1", {"method": "GET"})
        stub_store.save_fixture("fixture2", {"method": "POST"})

        fixtures = stub_store.list_fixtures()

        assert len(fixtures) == 2
        assert "fixture1" in fixtures
        assert "fixture2" in fixtures


class TestStubStoreUtility:
    """Tests for utility methods."""

    def test_stats(self, stub_store):
        # Empty store
        stats = stub_store.stats()
        assert stats["http_stubs"] == 0
        assert stats["db_stubs"] == 0
        assert stats["requests"] == 0

        # Add some data
        stub_store.save_http("fp1", {"status_code": 200})
        stub_store.save_http("fp2", {"status_code": 200})
        stub_store.save_db("db1", 0, [])
        stub_store.save_request("req1", {})

        stats = stub_store.stats()
        assert stats["http_stubs"] == 2
        assert stats["db_stubs"] == 1
        assert stats["requests"] == 1

    def test_clear(self, stub_store):
        stub_store.save_http("fp1", {"status_code": 200})
        stub_store.save_db("db1", 0, [])
        stub_store.save_request("req1", {})

        stub_store.clear()

        assert stub_store.stats()["http_stubs"] == 0
        assert stub_store.stats()["db_stubs"] == 0
        assert stub_store.stats()["requests"] == 0

    def test_directory_creation(self, temp_dir):
        # Use a non-existent subdirectory
        new_dir = temp_dir / "new" / "nested" / "stubs"
        store = StubStore(new_dir)

        # Saving should create directories
        store.save_http("fp1", {"status_code": 200})

        assert (new_dir / "http" / "fp1.json").exists()
