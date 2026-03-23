"""
Entrypoint for the Indexer service.

Usage:
    python -m fixture_service

This starts the long-running SQS polling loop that indexes fixtures
from S3 into Postgres.
"""

import logging
import sys
import time

import boto3
import psycopg2

from fixture_service.config import IndexerConfig
from fixture_service.indexer import run_indexer
from fixture_service.metrics import IndexerMetrics

# Maximum wait between reconnection attempts (seconds).
_MAX_RECONNECT_BACKOFF = 60


def _ensure_connection(database_url: str) -> "psycopg2.extensions.connection":
    """Create a new Postgres connection with autocommit disabled."""
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    return conn


def main() -> None:
    """Wire up dependencies and start the Indexer."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger(__name__)

    config = IndexerConfig.from_env()

    logger.info("Connecting to AWS", extra={"region": config.aws_region})
    s3_client = boto3.client("s3", region_name=config.aws_region)
    sqs_client = boto3.client("sqs", region_name=config.aws_region)

    metrics = IndexerMetrics()
    db_conn = None
    backoff = 1

    try:
        while True:
            # Establish or re-establish the Postgres connection.
            if db_conn is None:
                logger.info("Connecting to Postgres")
                try:
                    db_conn = _ensure_connection(config.database_url)
                    backoff = 1  # reset backoff on success
                except psycopg2.OperationalError as exc:
                    logger.error(
                        "Postgres connection failed, retrying",
                        extra={"error": str(exc), "backoff_s": backoff},
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_RECONNECT_BACKOFF)
                    continue

            try:
                run_indexer(s3_client, sqs_client, db_conn, config, metrics)
            except psycopg2.OperationalError as exc:
                logger.error(
                    "Postgres connection lost, reconnecting",
                    extra={"error": str(exc)},
                )
                try:
                    db_conn.close()
                except Exception:
                    pass
                db_conn = None
                time.sleep(backoff)
                backoff = min(backoff * 2, _MAX_RECONNECT_BACKOFF)
    except KeyboardInterrupt:
        logger.info("Indexer shutting down (KeyboardInterrupt)")
    finally:
        logger.info("Final metrics", extra={"metrics": metrics.snapshot()})
        if db_conn is not None:
            db_conn.close()
            logger.info("Postgres connection closed")


if __name__ == "__main__":
    main()
