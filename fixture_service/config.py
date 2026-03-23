"""
Shared configuration for the Fixture Service.

All configuration is driven by environment variables with sensible defaults.
Used by the Indexer (Task 3.4) and the Retrieval API (Task 3.5).
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class IndexerConfig:
    """Configuration for the Indexer service.

    Attributes:
        sqs_queue_url: Full URL of the SQS queue to poll.
        s3_bucket: S3 bucket name where fixtures are stored.
        database_url: Postgres connection string (DSN).
        dedup_window_hours: Hours within which duplicate content hashes are skipped.
        max_fixtures_per_endpoint_per_day: Hard ceiling per endpoint per day.
        sqs_max_messages: Max messages per SQS receive call (1-10).
        sqs_wait_seconds: Long-poll wait time in seconds (0-20).
        sqs_visibility_timeout: Seconds before an unacknowledged message becomes visible again.
        aws_region: AWS region for boto3 clients.
    """

    sqs_queue_url: str
    s3_bucket: str
    database_url: str
    dedup_window_hours: int
    max_fixtures_per_endpoint_per_day: int
    sqs_max_messages: int
    sqs_wait_seconds: int
    sqs_visibility_timeout: int
    aws_region: str

    @classmethod
    def from_env(cls) -> "IndexerConfig":
        """Build config from environment variables with defaults."""
        return cls(
            sqs_queue_url=os.environ.get("SQS_QUEUE_URL", ""),
            s3_bucket=os.environ.get("FIXTURES_S3_BUCKET", ""),
            database_url=os.environ.get("DATABASE_URL", ""),
            dedup_window_hours=int(os.environ.get("DEDUP_WINDOW_HOURS", "6")),
            max_fixtures_per_endpoint_per_day=int(
                os.environ.get("MAX_FIXTURES_PER_ENDPOINT_PER_DAY", "200")
            ),
            sqs_max_messages=int(os.environ.get("SQS_MAX_MESSAGES", "10")),
            sqs_wait_seconds=int(os.environ.get("SQS_WAIT_SECONDS", "20")),
            sqs_visibility_timeout=int(
                os.environ.get("SQS_VISIBILITY_TIMEOUT", "60")
            ),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        )
