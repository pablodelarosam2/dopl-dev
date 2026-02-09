"""Tests for sim_sdk.db_adapter module."""

import pytest

from sim_sdk.context import SimContext, SimMode, set_context
from sim_sdk.db_adapter import SimDB, SimStubMissError, SimWriteBlocked, _is_write_operation
from sim_sdk.store import StubStore


class TestWriteDetection:
    """Tests for write operation detection."""

    def test_insert_detected(self):
        assert _is_write_operation("INSERT INTO users VALUES (1)")
        assert _is_write_operation("  INSERT INTO users VALUES (1)")
        assert _is_write_operation("insert into users values (1)")

    def test_update_detected(self):
        assert _is_write_operation("UPDATE users SET name = 'test'")
        assert _is_write_operation("  update users set name = 'test'")

    def test_delete_detected(self):
        assert _is_write_operation("DELETE FROM users WHERE id = 1")
        assert _is_write_operation("  delete from users")

    def test_ddl_detected(self):
        assert _is_write_operation("DROP TABLE users")
        assert _is_write_operation("CREATE TABLE users (id INT)")
        assert _is_write_operation("ALTER TABLE users ADD COLUMN name TEXT")
        assert _is_write_operation("TRUNCATE users")

    def test_select_not_write(self):
        assert not _is_write_operation("SELECT * FROM users")
        assert not _is_write_operation("  select * from users")

    def test_with_cte_select(self):
        # CTEs starting with WITH are not writes
        assert not _is_write_operation("WITH cte AS (SELECT 1) SELECT * FROM cte")


class TestReplayMode:
    """Tests for REPLAY mode behavior."""

    def test_query_returns_stub(self, sim_context_replay):
        # Create a stub
        store = StubStore(sim_context_replay.stub_dir)

        from sim_sdk.canonicalize import fingerprint_sql
        fp = fingerprint_sql("SELECT * FROM users WHERE id = %s", (1,))

        store.save_db(fp, 0, [{"id": 1, "name": "Alice"}])

        # Query should return stub
        db = SimDB(dsn="postgresql://localhost/test")  # Won't actually connect
        rows = db.query("SELECT * FROM users WHERE id = %s", (1,))

        assert rows == [{"id": 1, "name": "Alice"}]

    def test_query_one_returns_first_row(self, sim_context_replay):
        store = StubStore(sim_context_replay.stub_dir)

        from sim_sdk.canonicalize import fingerprint_sql
        fp = fingerprint_sql("SELECT * FROM users", None)

        store.save_db(fp, 0, [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ])

        db = SimDB(dsn="postgresql://localhost/test")
        row = db.query_one("SELECT * FROM users")

        assert row == {"id": 1, "name": "Alice"}

    def test_query_one_returns_none_for_empty(self, sim_context_replay):
        store = StubStore(sim_context_replay.stub_dir)

        from sim_sdk.canonicalize import fingerprint_sql
        fp = fingerprint_sql("SELECT * FROM empty_table", None)

        store.save_db(fp, 0, [])

        db = SimDB(dsn="postgresql://localhost/test")
        row = db.query_one("SELECT * FROM empty_table")

        assert row is None

    def test_query_stub_miss_raises_error(self, sim_context_replay):
        db = SimDB(dsn="postgresql://localhost/test")

        with pytest.raises(SimStubMissError) as exc_info:
            db.query("SELECT * FROM missing_table")

        assert "missing_table" in str(exc_info.value.sql)

    def test_execute_write_blocked(self, sim_context_replay):
        db = SimDB(dsn="postgresql://localhost/test")

        with pytest.raises(SimWriteBlocked) as exc_info:
            db.execute("INSERT INTO users (name) VALUES (%s)", ("test",))

        assert "INSERT" in str(exc_info.value.sql)

    def test_execute_update_blocked(self, sim_context_replay):
        db = SimDB(dsn="postgresql://localhost/test")

        with pytest.raises(SimWriteBlocked):
            db.execute("UPDATE users SET name = %s WHERE id = %s", ("new", 1))

    def test_execute_delete_blocked(self, sim_context_replay):
        db = SimDB(dsn="postgresql://localhost/test")

        with pytest.raises(SimWriteBlocked):
            db.execute("DELETE FROM users WHERE id = %s", (1,))

    def test_executemany_blocked(self, sim_context_replay):
        db = SimDB(dsn="postgresql://localhost/test")

        with pytest.raises(SimWriteBlocked):
            db.executemany(
                "INSERT INTO users (name) VALUES (%s)",
                [("Alice",), ("Bob",)],
            )


class TestOrdinalTracking:
    """Tests for ordinal tracking with multiple queries."""

    def test_same_query_different_ordinals(self, sim_context_replay):
        store = StubStore(sim_context_replay.stub_dir)

        from sim_sdk.canonicalize import fingerprint_sql
        fp = fingerprint_sql("SELECT * FROM users", None)

        # Save stubs for ordinals 0, 1, 2
        store.save_db(fp, 0, [{"call": 0}])
        store.save_db(fp, 1, [{"call": 1}])
        store.save_db(fp, 2, [{"call": 2}])

        db = SimDB(dsn="postgresql://localhost/test")

        # Each call should get the next ordinal
        assert db.query("SELECT * FROM users") == [{"call": 0}]
        assert db.query("SELECT * FROM users") == [{"call": 1}]
        assert db.query("SELECT * FROM users") == [{"call": 2}]

    def test_different_queries_independent_ordinals(self, sim_context_replay):
        store = StubStore(sim_context_replay.stub_dir)

        from sim_sdk.canonicalize import fingerprint_sql
        fp1 = fingerprint_sql("SELECT * FROM users", None)
        fp2 = fingerprint_sql("SELECT * FROM products", None)

        store.save_db(fp1, 0, [{"table": "users", "call": 0}])
        store.save_db(fp2, 0, [{"table": "products", "call": 0}])
        store.save_db(fp1, 1, [{"table": "users", "call": 1}])

        db = SimDB(dsn="postgresql://localhost/test")

        # Interleaved queries
        assert db.query("SELECT * FROM users")[0]["call"] == 0
        assert db.query("SELECT * FROM products")[0]["call"] == 0
        assert db.query("SELECT * FROM users")[0]["call"] == 1


class TestTransactions:
    """Tests for transaction handling."""

    def test_transaction_noop_in_replay(self, sim_context_replay):
        store = StubStore(sim_context_replay.stub_dir)

        from sim_sdk.canonicalize import fingerprint_sql
        fp = fingerprint_sql("SELECT 1", None)
        store.save_db(fp, 0, [{"result": 1}])

        db = SimDB(dsn="postgresql://localhost/test")

        # Transaction should be a no-op but not error
        with db.transaction():
            result = db.query("SELECT 1")
            assert result == [{"result": 1}]


class TestContextManager:
    """Tests for SimDB as context manager."""

    def test_context_manager_closes(self, sim_context_replay):
        db = SimDB(dsn="postgresql://localhost/test")

        with db:
            pass  # Just test that it doesn't error

        # In replay mode, connection is None, so nothing to close
