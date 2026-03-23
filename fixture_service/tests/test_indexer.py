"""Tests for fixture_service.indexer."""

import json
import pytest


def _make_sqs_message(s3_key, bucket="fixtures-bucket", event_time="2026-03-21T14:30:00.000Z"):
    """Helper: build a realistic SQS message wrapping an S3 event notification."""
    s3_event = {
        "Records": [
            {
                "eventSource": "aws:s3",
                "eventTime": event_time,
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": s3_key, "size": 4096},
                },
            }
        ]
    }
    return {
        "MessageId": "msg-001",
        "ReceiptHandle": "receipt-001",
        "Body": json.dumps(s3_event),
    }


class TestParseS3Event:
    """Tests for parse_s3_event."""

    def test_extracts_s3_key(self):
        """Extracts the S3 object key from a standard S3 event notification."""
        from fixture_service.indexer import parse_s3_event

        msg = _make_sqs_message("fixtures/pricing-api/post_quote/2026-03-21/abc123.json")
        result = parse_s3_event(msg)

        assert result.s3_key == "fixtures/pricing-api/post_quote/2026-03-21/abc123.json"
        assert result.bucket == "fixtures-bucket"
        assert result.event_time == "2026-03-21T14:30:00.000Z"

    def test_extracts_from_different_bucket(self):
        """Works with any bucket name."""
        from fixture_service.indexer import parse_s3_event

        msg = _make_sqs_message(
            "fixtures/checkout/get_status/2026-03-22/def456.json",
            bucket="other-bucket",
        )
        result = parse_s3_event(msg)

        assert result.bucket == "other-bucket"
        assert result.s3_key == "fixtures/checkout/get_status/2026-03-22/def456.json"

    def test_raises_on_missing_records(self):
        """Raises ValueError when Records is missing."""
        from fixture_service.indexer import parse_s3_event

        msg = {"MessageId": "bad", "ReceiptHandle": "r", "Body": json.dumps({})}
        with pytest.raises(ValueError, match="No Records"):
            parse_s3_event(msg)

    def test_raises_on_empty_records(self):
        """Raises ValueError when Records is empty."""
        from fixture_service.indexer import parse_s3_event

        msg = {"MessageId": "bad", "ReceiptHandle": "r", "Body": json.dumps({"Records": []})}
        with pytest.raises(ValueError, match="No Records"):
            parse_s3_event(msg)

    def test_raises_on_malformed_body(self):
        """Raises ValueError when Body is not valid JSON."""
        from fixture_service.indexer import parse_s3_event

        msg = {"MessageId": "bad", "ReceiptHandle": "r", "Body": "not-json"}
        with pytest.raises(ValueError, match="Malformed SQS message body"):
            parse_s3_event(msg)

    def test_raises_on_missing_s3_key_in_record(self):
        """Raises ValueError when s3.object.key is missing."""
        from fixture_service.indexer import parse_s3_event

        s3_event = {
            "Records": [
                {
                    "eventSource": "aws:s3",
                    "eventTime": "2026-03-21T14:30:00.000Z",
                    "s3": {"bucket": {"name": "b"}},
                }
            ]
        }
        msg = {"MessageId": "bad", "ReceiptHandle": "r", "Body": json.dumps(s3_event)}
        with pytest.raises(ValueError, match="Missing s3 object key"):
            parse_s3_event(msg)


class TestParseS3Key:
    """Tests for parse_s3_key — extracts metadata from the structured S3 key."""

    def test_standard_key(self):
        """Parses a standard structured S3 key."""
        from fixture_service.indexer import parse_s3_key

        result = parse_s3_key("fixtures/pricing-api/post_quote/2026-03-21/abc123.json")

        assert result.service == "pricing-api"
        assert result.endpoint_key == "post_quote"
        assert result.date == "2026-03-21"
        assert result.fixture_id == "abc123"

    def test_nested_endpoint_key(self):
        """Parses a key where the endpoint_key has underscores."""
        from fixture_service.indexer import parse_s3_key

        result = parse_s3_key("fixtures/checkout-svc/get_checkout_status/2026-03-22/def456.json")

        assert result.service == "checkout-svc"
        assert result.endpoint_key == "get_checkout_status"
        assert result.date == "2026-03-22"
        assert result.fixture_id == "def456"

    def test_strips_json_extension_from_fixture_id(self):
        """fixture_id does not include the .json extension."""
        from fixture_service.indexer import parse_s3_key

        result = parse_s3_key("fixtures/svc/post_data/2026-01-01/uuid-value.json")
        assert result.fixture_id == "uuid-value"

    def test_raises_on_too_few_segments(self):
        """Raises ValueError if key has fewer than 5 segments."""
        from fixture_service.indexer import parse_s3_key

        with pytest.raises(ValueError, match="Invalid S3 key format"):
            parse_s3_key("fixtures/svc/only-three-parts")

    def test_raises_on_wrong_prefix(self):
        """Raises ValueError if key does not start with 'fixtures/'."""
        from fixture_service.indexer import parse_s3_key

        with pytest.raises(ValueError, match="Invalid S3 key format"):
            parse_s3_key("other/svc/post_data/2026-01-01/id.json")


class TestBuildEndpointKey:
    """Tests for build_endpoint_key — slugifies method + path to match daemon logic."""

    def test_simple_path(self):
        from fixture_service.indexer import build_endpoint_key

        assert build_endpoint_key("POST", "/quote") == "post_quote"

    def test_nested_path(self):
        from fixture_service.indexer import build_endpoint_key

        assert build_endpoint_key("GET", "/checkout/status") == "get_checkout_status"

    def test_strips_leading_trailing_underscores(self):
        from fixture_service.indexer import build_endpoint_key

        assert build_endpoint_key("GET", "/") == "get"

    def test_lowercases_method(self):
        from fixture_service.indexer import build_endpoint_key

        assert build_endpoint_key("DELETE", "/users/123") == "delete_users_123"

    def test_handles_trailing_slash(self):
        from fixture_service.indexer import build_endpoint_key

        assert build_endpoint_key("PUT", "/items/") == "put_items"


from unittest.mock import MagicMock, patch
import io


def _make_fixture_json(
    fixture_id="abc123",
    method="POST",
    path="/quote",
    recorded_at="2026-03-21T14:30:00Z",
    tags=None,
):
    """Helper: build a minimal fixture JSON dict."""
    return {
        "fixture_id": fixture_id,
        "qualname": "app.routes.create_quote",
        "run_id": "run-001",
        "recorded_at": recorded_at,
        "input": {"body": {"item": "widget", "qty": 10}},
        "input_fingerprint": "aaa",
        "output": {"price": 99.99},
        "output_fingerprint": "bbb",
        "stubs": [
            {"type": "http", "service": "tax-svc", "response": {"rate": 0.08}}
        ],
        "duration_ms": 42.5,
        "error": None,
        "ordinal": 0,
        "method": method,
        "path": path,
        "tags": tags or {},
    }


class TestDownloadAndParse:
    """Tests for download_and_parse — fetches fixture JSON from S3 via boto3."""

    def test_downloads_and_returns_dict(self):
        """Returns parsed dict when S3 returns valid JSON."""
        from fixture_service.indexer import download_and_parse

        fixture_data = _make_fixture_json()
        body_bytes = json.dumps(fixture_data).encode("utf-8")

        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {
            "Body": io.BytesIO(body_bytes),
        }

        result = download_and_parse(
            mock_s3,
            bucket="fixtures-bucket",
            s3_key="fixtures/pricing-api/post_quote/2026-03-21/abc123.json",
        )

        assert result["fixture_id"] == "abc123"
        assert result["output"] == {"price": 99.99}
        mock_s3.get_object.assert_called_once_with(
            Bucket="fixtures-bucket",
            Key="fixtures/pricing-api/post_quote/2026-03-21/abc123.json",
        )

    def test_raises_on_invalid_json(self):
        """Raises ValueError when S3 object is not valid JSON."""
        from fixture_service.indexer import download_and_parse

        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {
            "Body": io.BytesIO(b"not-json{{{"),
        }

        with pytest.raises(ValueError, match="Malformed fixture JSON"):
            download_and_parse(mock_s3, "bucket", "key.json")

    def test_propagates_s3_client_error(self):
        """Does not catch boto3 ClientError — lets caller handle retry logic."""
        from fixture_service.indexer import download_and_parse
        from botocore.exceptions import ClientError

        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
            "GetObject",
        )

        with pytest.raises(ClientError):
            download_and_parse(mock_s3, "bucket", "key.json")
