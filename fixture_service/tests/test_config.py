"""Tests for fixture_service.config."""

import os
import pytest
from unittest.mock import patch


class TestIndexerConfig:
    """Tests for IndexerConfig defaults and env-var overrides."""

    def test_defaults(self):
        """Config uses sensible defaults when no env vars are set."""
        from fixture_service.config import IndexerConfig

        with patch.dict(os.environ, {}, clear=True):
            cfg = IndexerConfig.from_env()

        assert cfg.sqs_queue_url == ""
        assert cfg.s3_bucket == ""
        assert cfg.database_url == ""
        assert cfg.dedup_window_hours == 6
        assert cfg.max_fixtures_per_endpoint_per_day == 200
        assert cfg.sqs_max_messages == 10
        assert cfg.sqs_wait_seconds == 20
        assert cfg.sqs_visibility_timeout == 60
        assert cfg.aws_region == "us-east-1"

    def test_env_overrides(self):
        """Config reads values from environment variables."""
        from fixture_service.config import IndexerConfig

        env = {
            "SQS_QUEUE_URL": "https://sqs.us-west-2.amazonaws.com/123/my-queue",
            "FIXTURES_S3_BUCKET": "my-fixtures-bucket",
            "DATABASE_URL": "postgresql://user:pass@host:5432/db",
            "DEDUP_WINDOW_HOURS": "12",
            "MAX_FIXTURES_PER_ENDPOINT_PER_DAY": "500",
            "SQS_MAX_MESSAGES": "5",
            "SQS_WAIT_SECONDS": "10",
            "SQS_VISIBILITY_TIMEOUT": "90",
            "AWS_REGION": "eu-west-1",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = IndexerConfig.from_env()

        assert cfg.sqs_queue_url == "https://sqs.us-west-2.amazonaws.com/123/my-queue"
        assert cfg.s3_bucket == "my-fixtures-bucket"
        assert cfg.database_url == "postgresql://user:pass@host:5432/db"
        assert cfg.dedup_window_hours == 12
        assert cfg.max_fixtures_per_endpoint_per_day == 500
        assert cfg.sqs_max_messages == 5
        assert cfg.sqs_wait_seconds == 10
        assert cfg.sqs_visibility_timeout == 90
        assert cfg.aws_region == "eu-west-1"

    def test_dedup_window_hours_type_coercion(self):
        """DEDUP_WINDOW_HOURS is coerced to int."""
        from fixture_service.config import IndexerConfig

        with patch.dict(os.environ, {"DEDUP_WINDOW_HOURS": "24"}, clear=True):
            cfg = IndexerConfig.from_env()

        assert cfg.dedup_window_hours == 24
        assert isinstance(cfg.dedup_window_hours, int)
