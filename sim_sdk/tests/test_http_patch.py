"""Tests for sim_sdk.http_patch module."""

import json

import pytest
import responses

from sim_sdk.context import SimContext, SimMode, set_context
from sim_sdk.http_patch import (
    RequestsPatch,
    StubMissError,
    is_patched,
    patch_requests,
    unpatch_requests,
)
from sim_sdk.store import StubStore


class TestPatchUnpatch:
    """Tests for patching and unpatching requests."""

    def test_patch_and_unpatch(self):
        assert not is_patched()

        patch_requests()
        assert is_patched()

        unpatch_requests()
        assert not is_patched()

    def test_double_patch_safe(self):
        patch_requests()
        patch_requests()  # Should be safe
        assert is_patched()

        unpatch_requests()
        assert not is_patched()

    def test_double_unpatch_safe(self):
        unpatch_requests()  # Should be safe even if not patched
        unpatch_requests()

    def test_context_manager(self):
        assert not is_patched()

        with RequestsPatch():
            assert is_patched()

        assert not is_patched()


class TestOffMode:
    """Tests for behavior in OFF mode."""

    @responses.activate
    def test_real_request_made(self, sim_context_off):
        patch_requests()

        try:
            responses.add(
                responses.GET,
                "https://api.example.com/data",
                json={"result": "real"},
                status=200,
            )

            import requests
            response = requests.get("https://api.example.com/data")

            assert response.status_code == 200
            assert response.json() == {"result": "real"}
        finally:
            unpatch_requests()


class TestRecordMode:
    """Tests for RECORD mode behavior."""

    @responses.activate
    def test_request_recorded(self, sim_context_record):
        patch_requests()

        try:
            responses.add(
                responses.GET,
                "https://api.example.com/data",
                json={"result": "recorded"},
                status=200,
            )

            import requests
            response = requests.get("https://api.example.com/data")

            assert response.status_code == 200
            assert response.json() == {"result": "recorded"}

            # Check stub was saved
            store = StubStore(sim_context_record.stub_dir)
            stats = store.stats()
            assert stats["http_stubs"] >= 1
        finally:
            unpatch_requests()

    @responses.activate
    def test_post_request_recorded(self, sim_context_record):
        patch_requests()

        try:
            responses.add(
                responses.POST,
                "https://api.example.com/submit",
                json={"id": 123},
                status=201,
            )

            import requests
            response = requests.post(
                "https://api.example.com/submit",
                json={"name": "test"},
            )

            assert response.status_code == 201

            store = StubStore(sim_context_record.stub_dir)
            assert store.stats()["http_stubs"] >= 1
        finally:
            unpatch_requests()


class TestReplayMode:
    """Tests for REPLAY mode behavior."""

    def test_stub_miss_raises_error(self, sim_context_replay):
        patch_requests()

        try:
            import requests

            with pytest.raises(StubMissError) as exc_info:
                requests.get("https://api.example.com/missing")

            assert "missing" in str(exc_info.value.url)
            assert exc_info.value.method.upper() == "GET"
        finally:
            unpatch_requests()

    def test_stub_hit_returns_response(self, sim_context_replay):
        # First, create a stub
        store = StubStore(sim_context_replay.stub_dir)

        # We need to create stub with the correct fingerprint
        # For simplicity, let's save with a known fingerprint pattern
        from sim_sdk.http_patch import _fingerprint_request
        fp = _fingerprint_request("GET", "https://api.example.com/data", {})

        store.save_http(
            f"{fp}_0",
            {
                "status_code": 200,
                "headers": {"content-type": "application/json"},
                "body": {"result": "stubbed"},
            },
        )

        patch_requests()

        try:
            import requests
            response = requests.get("https://api.example.com/data")

            assert response.status_code == 200
            assert response.json() == {"result": "stubbed"}
        finally:
            unpatch_requests()


class TestFingerprinting:
    """Tests for request fingerprinting."""

    def test_same_request_same_fingerprint(self):
        from sim_sdk.http_patch import _fingerprint_request

        fp1 = _fingerprint_request(
            "POST",
            "https://api.example.com/data",
            {"json": {"a": 1, "b": 2}},
        )
        fp2 = _fingerprint_request(
            "POST",
            "https://api.example.com/data",
            {"json": {"b": 2, "a": 1}},  # Different order, same content
        )

        assert fp1 == fp2

    def test_different_body_different_fingerprint(self):
        from sim_sdk.http_patch import _fingerprint_request

        fp1 = _fingerprint_request(
            "POST",
            "https://api.example.com/data",
            {"json": {"value": 1}},
        )
        fp2 = _fingerprint_request(
            "POST",
            "https://api.example.com/data",
            {"json": {"value": 2}},
        )

        assert fp1 != fp2

    def test_headers_excluded_by_default(self):
        from sim_sdk.http_patch import _fingerprint_request

        fp1 = _fingerprint_request(
            "GET",
            "https://api.example.com/data",
            {"headers": {"Authorization": "Bearer token1"}},
        )
        fp2 = _fingerprint_request(
            "GET",
            "https://api.example.com/data",
            {"headers": {"Authorization": "Bearer token2"}},
        )

        # Authorization is excluded, so fingerprints should match
        assert fp1 == fp2


class TestOrdinalTracking:
    """Tests for ordinal tracking with multiple calls."""

    @responses.activate
    def test_multiple_calls_recorded_separately(self, sim_context_record):
        patch_requests()

        try:
            # Same endpoint called twice with same params
            responses.add(
                responses.GET,
                "https://api.example.com/data",
                json={"call": 1},
                status=200,
            )
            responses.add(
                responses.GET,
                "https://api.example.com/data",
                json={"call": 2},
                status=200,
            )

            import requests

            resp1 = requests.get("https://api.example.com/data")
            resp2 = requests.get("https://api.example.com/data")

            assert resp1.json()["call"] == 1
            assert resp2.json()["call"] == 2

            # Both should be recorded
            store = StubStore(sim_context_record.stub_dir)
            assert store.stats()["http_stubs"] >= 2
        finally:
            unpatch_requests()
