"""
Entrypoint for the Indexer service.

Usage:
    python -m fixture_service

This starts the long-running SQS polling loop that indexes fixtures
from S3 into Postgres.
"""

import logging
import sys

import boto3
import psycopg2

from fixture_service.config import IndexerConfig
from fixture_service.indexer import run_indexer
from fixture_service.metrics import IndexerMetrics


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

    logger.info("Connecting to Postgres")
    db_conn = psycopg2.connect(config.database_url)
    db_conn.autocommit = False

    metrics = IndexerMetrics()

    try:
        run_indexer(s3_client, sqs_client, db_conn, config, metrics)
    except KeyboardInterrupt:
        logger.info("Indexer shutting down (KeyboardInterrupt)")
    finally:
        logger.info("Final metrics", extra={"metrics": metrics.snapshot()})
        db_conn.close()
        logger.info("Postgres connection closed")


if __name__ == "__main__":
    main()
