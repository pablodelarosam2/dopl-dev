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
