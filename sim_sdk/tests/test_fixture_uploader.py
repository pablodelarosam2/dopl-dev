"""Tests for S3 structured key generation."""

from datetime import datetime, timezone

import pytest

from sim_sdk.fixture_uploader import build_s3_key, build_endpoint_key


class TestBuildEndpointKey:
    """build_endpoint_key slugifies method + path into a stable key."""

    def test_simple_post(self):
        assert build_endpoint_key("POST", "/quote") == "post_quote"

    def test_get_with_nested_path(self):
        assert build_endpoint_key("GET", "/checkout/status") == "get_checkout_status"

    def test_strips_leading_trailing_underscores(self):
        assert build_endpoint_key("GET", "/") == "get"

    def test_multiple_slashes(self):
        assert build_endpoint_key("PUT", "/api/v1/users/") == "put_api_v1_users"

    def test_case_insensitive(self):
        assert build_endpoint_key("POST", "/Quote") == "post_quote"

    def test_method_case_insensitive(self):
        assert build_endpoint_key("post", "/quote") == "post_quote"

    def test_already_lowercase(self):
        assert build_endpoint_key("delete", "/items") == "delete_items"


class TestBuildS3Key:
    """build_s3_key produces the structured S3 prefix layout."""

    def test_basic_key(self):
        recorded_at = datetime(2026, 3, 21, 14, 30, 0, tzinfo=timezone.utc)
        key = build_s3_key("pricing-api", "POST", "/quote", "abc-123", recorded_at)
        assert key == "fixtures/pricing-api/post_quote/2026-03-21/abc-123.json"

    def test_nested_path(self):
        recorded_at = datetime(2026, 1, 15, tzinfo=timezone.utc)
        key = build_s3_key("my-svc", "GET", "/checkout/status", "fix-001", recorded_at)
        assert key == "fixtures/my-svc/get_checkout_status/2026-01-15/fix-001.json"

    def test_root_path(self):
        recorded_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        key = build_s3_key("svc", "GET", "/", "id-1", recorded_at)
        assert key == "fixtures/svc/get/2026-06-01/id-1.json"

    def test_trailing_slash_path(self):
        recorded_at = datetime(2026, 12, 31, tzinfo=timezone.utc)
        key = build_s3_key("svc", "PUT", "/users/", "id-2", recorded_at)
        assert key == "fixtures/svc/put_users/2026-12-31/id-2.json"

    def test_date_formatting(self):
        recorded_at = datetime(2026, 3, 5, tzinfo=timezone.utc)
        key = build_s3_key("svc", "POST", "/a", "id-x", recorded_at)
        assert "2026-03-05" in key


class TestZeroDependencies:
    """fixture_uploader.py must not import banned modules."""

    def test_no_banned_imports(self):
        from pathlib import Path
        import sim_sdk.fixture_uploader as mod
        source = Path(mod.__file__).read_text()
        banned = [
            "flask", "django", "fastapi", "requests", "httpx",
            "aiohttp", "psycopg2", "sqlalchemy",
        ]
        for module in banned:
            assert f"import {module}" not in source
            assert f"from {module}" not in source
