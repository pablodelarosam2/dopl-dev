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


class TestComputeContentHash:
    """Tests for compute_content_hash — SHA-256 of canonical fixture body."""

    def test_returns_64_char_hex(self):
        """Hash is a 64-character hex string (SHA-256)."""
        from fixture_service.indexer import compute_content_hash

        fixture = _make_fixture_json()
        h = compute_content_hash(fixture)

        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        """Same fixture always produces the same hash."""
        from fixture_service.indexer import compute_content_hash

        fixture = _make_fixture_json()
        assert compute_content_hash(fixture) == compute_content_hash(fixture)

    def test_key_order_independent(self):
        """Dict key order does not affect the hash (canonical JSON sorts keys)."""
        from fixture_service.indexer import compute_content_hash

        f1 = {"input": {"a": 1, "b": 2}, "stubs": []}
        f2 = {"stubs": [], "input": {"b": 2, "a": 1}}
        assert compute_content_hash(f1) == compute_content_hash(f2)

    def test_different_content_different_hash(self):
        """Different fixture content produces a different hash."""
        from fixture_service.indexer import compute_content_hash

        f1 = _make_fixture_json()
        f2 = _make_fixture_json()
        f2["output"] = {"price": 0.01}  # different output
        assert compute_content_hash(f1) != compute_content_hash(f2)

    def test_hashes_input_and_stubs(self):
        """Hash covers both input and stubs (the dedup identity)."""
        from fixture_service.indexer import compute_content_hash

        f1 = _make_fixture_json()
        f2 = _make_fixture_json()
        f2["stubs"] = []  # remove stubs
        assert compute_content_hash(f1) != compute_content_hash(f2)


class TestExtractMetadata:
    """Tests for extract_metadata — combines S3 key metadata with fixture JSON fields."""

    def test_extracts_all_fields(self):
        """Extracts method, path, service, endpoint_key, recorded_at, tags from fixture + S3 key."""
        from fixture_service.indexer import extract_metadata, S3KeyMetadata

        fixture = _make_fixture_json(method="POST", path="/quote", recorded_at="2026-03-21T14:30:00Z")
        key_meta = S3KeyMetadata(service="pricing-api", endpoint_key="post_quote", date="2026-03-21", fixture_id="abc123")

        meta = extract_metadata(fixture, key_meta)

        assert meta.service == "pricing-api"
        assert meta.method == "POST"
        assert meta.path == "/quote"
        assert meta.endpoint_key == "post_quote"
        assert meta.recorded_at == "2026-03-21T14:30:00Z"
        assert meta.tags == {}
        assert meta.fixture_id == "abc123"

    def test_uses_s3_key_service_not_fixture(self):
        """Service comes from S3 key prefix, not fixture body."""
        from fixture_service.indexer import extract_metadata, S3KeyMetadata

        fixture = _make_fixture_json()
        key_meta = S3KeyMetadata(service="from-s3-key", endpoint_key="post_quote", date="2026-03-21", fixture_id="abc123")

        meta = extract_metadata(fixture, key_meta)
        assert meta.service == "from-s3-key"

    def test_extracts_tags_from_fixture(self):
        """Tags come from the optional 'tags' field in fixture JSON."""
        from fixture_service.indexer import extract_metadata, S3KeyMetadata

        fixture = _make_fixture_json(tags={"scenario": "premium_user"})
        key_meta = S3KeyMetadata(service="svc", endpoint_key="post_quote", date="2026-03-21", fixture_id="abc123")

        meta = extract_metadata(fixture, key_meta)
        assert meta.tags == {"scenario": "premium_user"}

    def test_defaults_tags_to_empty_dict(self):
        """Tags default to {} when not present in fixture JSON."""
        from fixture_service.indexer import extract_metadata, S3KeyMetadata

        fixture = _make_fixture_json()
        del fixture["tags"]
        key_meta = S3KeyMetadata(service="svc", endpoint_key="post_quote", date="2026-03-21", fixture_id="abc123")

        meta = extract_metadata(fixture, key_meta)
        assert meta.tags == {}

    def test_uses_endpoint_key_from_s3(self):
        """endpoint_key comes from the S3 key, not recomputed from fixture method+path."""
        from fixture_service.indexer import extract_metadata, S3KeyMetadata

        fixture = _make_fixture_json(method="POST", path="/quote")
        key_meta = S3KeyMetadata(service="svc", endpoint_key="custom_key", date="2026-03-21", fixture_id="abc123")

        meta = extract_metadata(fixture, key_meta)
        assert meta.endpoint_key == "custom_key"

    def test_falls_back_to_event_time_for_recorded_at(self):
        """If fixture JSON has no recorded_at, falls back to event_time from S3 key date."""
        from fixture_service.indexer import extract_metadata, S3KeyMetadata

        fixture = _make_fixture_json()
        del fixture["recorded_at"]
        key_meta = S3KeyMetadata(service="svc", endpoint_key="post_quote", date="2026-03-21", fixture_id="abc123")

        meta = extract_metadata(fixture, key_meta, event_time="2026-03-21T15:00:00Z")
        assert meta.recorded_at == "2026-03-21T15:00:00Z"


class TestIsDuplicate:
    """Tests for is_duplicate — queries Postgres for existing content_hash within dedup window."""

    def test_returns_false_when_no_match(self):
        """Not a duplicate when no rows match the content_hash within the window."""
        from fixture_service.indexer import is_duplicate

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None

        result = is_duplicate(mock_cursor, "abc123hash", window_hours=6)

        assert result is False
        mock_cursor.execute.assert_called_once()
        # Verify the SQL query checks content_hash within time window
        sql = mock_cursor.execute.call_args[0][0]
        assert "content_hash" in sql
        assert "recorded_at" in sql
        assert "interval" in sql

    def test_returns_true_when_match_exists(self):
        """Is a duplicate when a row with the same content_hash exists within the window."""
        from fixture_service.indexer import is_duplicate

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)

        result = is_duplicate(mock_cursor, "abc123hash", window_hours=6)

        assert result is True

    def test_passes_correct_parameters(self):
        """Passes content_hash and window_hours to the SQL query."""
        from fixture_service.indexer import is_duplicate

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None

        is_duplicate(mock_cursor, "myhash", window_hours=12)

        params = mock_cursor.execute.call_args[0][1]
        assert params[0] == "myhash"
        assert params[1] == 12

    def test_uses_configurable_window(self):
        """The dedup window is configurable (not hardcoded to 6 hours)."""
        from fixture_service.indexer import is_duplicate

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None

        is_duplicate(mock_cursor, "hash", window_hours=24)

        params = mock_cursor.execute.call_args[0][1]
        assert params[1] == 24


class TestInsertIndexRow:
    """Tests for insert_index_row — inserts a row into fixtures_index."""

    def test_inserts_all_columns(self):
        """Inserts a row with all required columns."""
        from fixture_service.indexer import insert_index_row, FixtureMetadata

        mock_cursor = MagicMock()
        meta = FixtureMetadata(
            service="pricing-api",
            method="POST",
            path="/quote",
            endpoint_key="post_quote",
            recorded_at="2026-03-21T14:30:00Z",
            tags={"scenario": "premium"},
            fixture_id="abc123",
        )

        insert_index_row(
            mock_cursor,
            meta,
            s3_key="fixtures/pricing-api/post_quote/2026-03-21/abc123.json",
            content_hash="deadbeef" * 8,
            s3_bucket="fixtures-bucket",
        )

        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        params = mock_cursor.execute.call_args[0][1]

        # Verify SQL targets the right table and columns
        assert "INSERT INTO fixtures_index" in sql
        assert "service" in sql
        assert "method" in sql
        assert "path" in sql
        assert "endpoint_key" in sql
        assert "content_hash" in sql
        assert "s3_uri" in sql
        assert "recorded_at" in sql
        assert "tags" in sql

        # Verify parameter values
        assert params["service"] == "pricing-api"
        assert params["method"] == "POST"
        assert params["path"] == "/quote"
        assert params["endpoint_key"] == "post_quote"
        assert params["content_hash"] == "deadbeef" * 8
        assert params["s3_uri"] == "s3://fixtures-bucket/fixtures/pricing-api/post_quote/2026-03-21/abc123.json"
        assert params["recorded_at"] == "2026-03-21T14:30:00Z"

    def test_s3_uri_format(self):
        """s3_uri is formatted as s3://{bucket}/{key}."""
        from fixture_service.indexer import insert_index_row, FixtureMetadata

        mock_cursor = MagicMock()
        meta = FixtureMetadata(
            service="svc", method="GET", path="/health",
            endpoint_key="get_health", recorded_at="2026-03-21T14:30:00Z",
            tags={}, fixture_id="id1",
        )

        insert_index_row(mock_cursor, meta, "fixtures/svc/get_health/2026-03-21/id1.json", "hash", "my-bucket")

        params = mock_cursor.execute.call_args[0][1]
        assert params["s3_uri"] == "s3://my-bucket/fixtures/svc/get_health/2026-03-21/id1.json"

    def test_tags_passed_as_json(self):
        """Tags are serialized as a JSON string for the JSONB column."""
        from fixture_service.indexer import insert_index_row, FixtureMetadata

        mock_cursor = MagicMock()
        meta = FixtureMetadata(
            service="svc", method="GET", path="/x",
            endpoint_key="get_x", recorded_at="2026-03-21T14:30:00Z",
            tags={"env": "staging", "version": "2.1"}, fixture_id="id1",
        )

        insert_index_row(mock_cursor, meta, "key", "hash", "bucket")

        params = mock_cursor.execute.call_args[0][1]
        tags_value = params["tags"]
        # Should be a JSON string for psycopg2 JSONB compatibility
        parsed = json.loads(tags_value)
        assert parsed == {"env": "staging", "version": "2.1"}


class TestIsDailyCapReached:
    """Tests for is_daily_cap_reached — enforces max_fixtures_per_endpoint_per_day."""

    def test_returns_false_when_under_cap(self):
        """Not capped when today's count is below the maximum."""
        from fixture_service.indexer import is_daily_cap_reached

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (50,)

        result = is_daily_cap_reached(mock_cursor, "pricing-api", "post_quote", max_per_day=200)

        assert result is False

    def test_returns_true_when_at_cap(self):
        """Capped when today's count equals the maximum."""
        from fixture_service.indexer import is_daily_cap_reached

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (200,)

        result = is_daily_cap_reached(mock_cursor, "pricing-api", "post_quote", max_per_day=200)

        assert result is True

    def test_returns_true_when_over_cap(self):
        """Capped when today's count exceeds the maximum."""
        from fixture_service.indexer import is_daily_cap_reached

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (250,)

        result = is_daily_cap_reached(mock_cursor, "pricing-api", "post_quote", max_per_day=200)

        assert result is True

    def test_passes_correct_parameters(self):
        """Passes service and endpoint_key to the query."""
        from fixture_service.indexer import is_daily_cap_reached

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)

        is_daily_cap_reached(mock_cursor, "my-svc", "get_data", max_per_day=100)

        params = mock_cursor.execute.call_args[0][1]
        assert params[0] == "my-svc"
        assert params[1] == "get_data"

    def test_returns_false_when_count_is_zero(self):
        """Not capped when no fixtures have been indexed today."""
        from fixture_service.indexer import is_daily_cap_reached

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)

        result = is_daily_cap_reached(mock_cursor, "svc", "ep", max_per_day=200)

        assert result is False


class TestProcessMessage:
    """Tests for process_message — processes a single SQS message end-to-end."""

    def _make_deps(self, fixture_json=None, is_dup=False, is_capped=False):
        """Helper: build mock dependencies for process_message."""
        from fixture_service.config import IndexerConfig
        from fixture_service.metrics import IndexerMetrics

        if fixture_json is None:
            fixture_json = _make_fixture_json()

        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {
            "Body": io.BytesIO(json.dumps(fixture_json).encode("utf-8")),
        }

        mock_sqs = MagicMock()

        mock_cursor = MagicMock()
        # Set up fetchone side_effect for the pipeline's multiple queries
        if is_dup:
            # is_duplicate returns a row -> skip
            mock_cursor.fetchone.side_effect = [(1,)]
        elif is_capped:
            # is_duplicate returns None (not dup), is_daily_cap_reached returns (200,) -> capped
            mock_cursor.fetchone.side_effect = [None, (200,)]
        else:
            # is_duplicate returns None (not dup), is_daily_cap_reached returns (0,) -> not capped
            mock_cursor.fetchone.side_effect = [None, (0,)]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        config = IndexerConfig(
            sqs_queue_url="https://sqs.example.com/queue",
            s3_bucket="fixtures-bucket",
            database_url="postgresql://test",
            dedup_window_hours=6,
            max_fixtures_per_endpoint_per_day=200,
            sqs_max_messages=10,
            sqs_wait_seconds=20,
            sqs_visibility_timeout=60,
            aws_region="us-east-1",
        )

        metrics = IndexerMetrics()

        return mock_s3, mock_sqs, mock_conn, mock_cursor, config, metrics

    def test_happy_path_inserts_row_and_deletes_message(self):
        """On valid fixture: downloads, parses, dedup-checks, inserts, deletes SQS message."""
        from fixture_service.indexer import process_message

        fixture_json = _make_fixture_json()
        s3_key = "fixtures/pricing-api/post_quote/2026-03-21/abc123.json"
        sqs_msg = _make_sqs_message(s3_key)

        mock_s3, mock_sqs, mock_conn, mock_cursor, config, metrics = self._make_deps(
            fixture_json=fixture_json, is_dup=False,
        )

        process_message(sqs_msg, mock_s3, mock_sqs, mock_conn, config, metrics)

        # S3 download happened
        mock_s3.get_object.assert_called_once_with(Bucket="fixtures-bucket", Key=s3_key)
        # Insert happened (is_duplicate returned None = not a dup)
        assert mock_cursor.execute.call_count >= 2  # dedup check + insert (possibly cap check)
        # SQS message deleted
        mock_sqs.delete_message.assert_called_once_with(
            QueueUrl=config.sqs_queue_url,
            ReceiptHandle=sqs_msg["ReceiptHandle"],
        )
        # Metrics
        assert metrics.messages_processed == 1
        assert metrics.rows_inserted == 1

    def test_duplicate_skips_insert_but_deletes_message(self):
        """On duplicate: skips insert, but still deletes SQS message (idempotent)."""
        from fixture_service.indexer import process_message

        sqs_msg = _make_sqs_message("fixtures/svc/post_x/2026-03-21/id1.json")
        mock_s3, mock_sqs, mock_conn, mock_cursor, config, metrics = self._make_deps(is_dup=True)

        process_message(sqs_msg, mock_s3, mock_sqs, mock_conn, config, metrics)

        # SQS message still deleted (dedup is not an error)
        mock_sqs.delete_message.assert_called_once()
        # Metrics
        assert metrics.messages_processed == 1
        assert metrics.duplicates_skipped == 1
        assert metrics.rows_inserted == 0

    def test_malformed_json_logs_and_deletes_message(self):
        """On malformed JSON: logs error, deletes message (don't retry garbage)."""
        from fixture_service.indexer import process_message

        sqs_msg = _make_sqs_message("fixtures/svc/post_x/2026-03-21/id1.json")
        mock_s3, mock_sqs, mock_conn, mock_cursor, config, metrics = self._make_deps()

        # Override S3 to return invalid JSON
        mock_s3.get_object.return_value = {
            "Body": io.BytesIO(b"NOT VALID JSON"),
        }

        process_message(sqs_msg, mock_s3, mock_sqs, mock_conn, config, metrics)

        # Message deleted (garbage should not be retried)
        mock_sqs.delete_message.assert_called_once()
        # Metrics
        assert metrics.parse_errors == 1
        assert metrics.rows_inserted == 0

    def test_s3_error_does_not_delete_message(self):
        """On S3 failure: does NOT delete SQS message (let SQS retry)."""
        from fixture_service.indexer import process_message
        from botocore.exceptions import ClientError

        sqs_msg = _make_sqs_message("fixtures/svc/post_x/2026-03-21/id1.json")
        mock_s3, mock_sqs, mock_conn, mock_cursor, config, metrics = self._make_deps()

        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "Internal"}}, "GetObject"
        )

        process_message(sqs_msg, mock_s3, mock_sqs, mock_conn, config, metrics)

        # Message NOT deleted — SQS will retry after visibility timeout
        mock_sqs.delete_message.assert_not_called()

    def test_postgres_error_does_not_delete_message(self):
        """On Postgres failure: does NOT delete SQS message (let SQS retry)."""
        from fixture_service.indexer import process_message
        import psycopg2

        sqs_msg = _make_sqs_message("fixtures/svc/post_x/2026-03-21/id1.json")
        mock_s3, mock_sqs, mock_conn, mock_cursor, config, metrics = self._make_deps()

        # First cursor.execute call (is_duplicate) raises
        mock_cursor.execute.side_effect = psycopg2.OperationalError("connection refused")

        process_message(sqs_msg, mock_s3, mock_sqs, mock_conn, config, metrics)

        # Message NOT deleted — SQS will retry
        mock_sqs.delete_message.assert_not_called()

    def test_malformed_sqs_body_deletes_message(self):
        """On unparseable SQS body: deletes message (garbage, don't retry)."""
        from fixture_service.indexer import process_message

        sqs_msg = {"MessageId": "bad", "ReceiptHandle": "r", "Body": "not-json"}
        mock_s3, mock_sqs, mock_conn, mock_cursor, config, metrics = self._make_deps()

        process_message(sqs_msg, mock_s3, mock_sqs, mock_conn, config, metrics)

        # Message deleted — malformed event is garbage
        mock_sqs.delete_message.assert_called_once()
        assert metrics.parse_errors == 1

    def test_daily_cap_reached_skips_insert_and_deletes_message(self):
        """On daily cap reached: skips insert, deletes message."""
        from fixture_service.indexer import process_message

        sqs_msg = _make_sqs_message("fixtures/svc/post_x/2026-03-21/id1.json")
        mock_s3, mock_sqs, mock_conn, mock_cursor, config, metrics = self._make_deps(is_capped=True)

        process_message(sqs_msg, mock_s3, mock_sqs, mock_conn, config, metrics)

        # Message deleted (cap reached is not an error, just a skip)
        mock_sqs.delete_message.assert_called_once()
        assert metrics.daily_cap_skipped == 1
        assert metrics.rows_inserted == 0
