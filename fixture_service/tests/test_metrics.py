"""Tests for fixture_service.metrics."""


class TestIndexerMetrics:
    """Tests for the IndexerMetrics counter class."""

    def test_initial_values_are_zero(self):
        from fixture_service.metrics import IndexerMetrics

        m = IndexerMetrics()
        assert m.messages_processed == 0
        assert m.rows_inserted == 0
        assert m.duplicates_skipped == 0
        assert m.parse_errors == 0
        assert m.daily_cap_skipped == 0

    def test_increment_messages_processed(self):
        from fixture_service.metrics import IndexerMetrics

        m = IndexerMetrics()
        m.inc_messages_processed()
        m.inc_messages_processed()
        assert m.messages_processed == 2

    def test_increment_rows_inserted(self):
        from fixture_service.metrics import IndexerMetrics

        m = IndexerMetrics()
        m.inc_rows_inserted()
        assert m.rows_inserted == 1

    def test_increment_duplicates_skipped(self):
        from fixture_service.metrics import IndexerMetrics

        m = IndexerMetrics()
        m.inc_duplicates_skipped()
        assert m.duplicates_skipped == 1

    def test_increment_parse_errors(self):
        from fixture_service.metrics import IndexerMetrics

        m = IndexerMetrics()
        m.inc_parse_errors()
        assert m.parse_errors == 1

    def test_increment_daily_cap_skipped(self):
        from fixture_service.metrics import IndexerMetrics

        m = IndexerMetrics()
        m.inc_daily_cap_skipped()
        assert m.daily_cap_skipped == 1

    def test_snapshot_returns_dict(self):
        from fixture_service.metrics import IndexerMetrics

        m = IndexerMetrics()
        m.inc_messages_processed()
        m.inc_rows_inserted()
        m.inc_duplicates_skipped()

        snap = m.snapshot()
        assert snap == {
            "indexer_messages_processed": 1,
            "indexer_rows_inserted": 1,
            "indexer_duplicates_skipped": 1,
            "indexer_parse_errors": 0,
            "indexer_daily_cap_skipped": 0,
        }

    def test_reset(self):
        from fixture_service.metrics import IndexerMetrics

        m = IndexerMetrics()
        m.inc_messages_processed()
        m.inc_rows_inserted()
        m.reset()

        assert m.messages_processed == 0
        assert m.rows_inserted == 0
