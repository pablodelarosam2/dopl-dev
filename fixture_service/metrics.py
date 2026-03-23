"""
Indexer metrics — simple in-process counters.

Provides the counters specified in the Task 3.4 exit criteria:
  - indexer_messages_processed
  - indexer_rows_inserted
  - indexer_duplicates_skipped
  - indexer_parse_errors

Plus an additional counter for daily cap enforcement:
  - indexer_daily_cap_skipped

These are in-process counters suitable for periodic logging.
A future iteration can push these to CloudWatch, StatsD, or Prometheus.
"""

import threading


class IndexerMetrics:
    """Thread-safe in-process counters for the Indexer service."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.messages_processed: int = 0
        self.rows_inserted: int = 0
        self.duplicates_skipped: int = 0
        self.parse_errors: int = 0
        self.daily_cap_skipped: int = 0

    def inc_messages_processed(self) -> None:
        with self._lock:
            self.messages_processed += 1

    def inc_rows_inserted(self) -> None:
        with self._lock:
            self.rows_inserted += 1

    def inc_duplicates_skipped(self) -> None:
        with self._lock:
            self.duplicates_skipped += 1

    def inc_parse_errors(self) -> None:
        with self._lock:
            self.parse_errors += 1

    def inc_daily_cap_skipped(self) -> None:
        with self._lock:
            self.daily_cap_skipped += 1

    def snapshot(self) -> dict:
        """Return a point-in-time snapshot of all counters as a dict.

        Keys match the metric names from the Task 3.4 spec.
        """
        with self._lock:
            return {
                "indexer_messages_processed": self.messages_processed,
                "indexer_rows_inserted": self.rows_inserted,
                "indexer_duplicates_skipped": self.duplicates_skipped,
                "indexer_parse_errors": self.parse_errors,
                "indexer_daily_cap_skipped": self.daily_cap_skipped,
            }

    def reset(self) -> None:
        """Reset all counters to zero."""
        with self._lock:
            self.messages_processed = 0
            self.rows_inserted = 0
            self.duplicates_skipped = 0
            self.parse_errors = 0
            self.daily_cap_skipped = 0
