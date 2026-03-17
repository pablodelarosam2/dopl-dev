"""
Tests for T5: sim_db() DB Context Manager

Uses FakeDB classes — NOT psycopg2, SQLAlchemy, or any real database driver.

Covers all acceptance criteria:
1. Record mode — queries execute via underlying DB object, results captured
2. Replay mode — queries return recorded rows from ReplayContext, real DB not called
3. Stub miss returns [] with diagnostic log (soft fail)
4. SimWriteBlockedError on write statements in replay mode
5. Ordinal correctly tracks repeated identical queries
6. Outside the with block, original DB object is completely unmodified
7. Works with any object that has .query() or .execute()
8. Zero imports from any DB driver
9. Off mode — passthrough
10. Round-trip — record then replay via StubStore returns identical rows
"""

import asyncio
import inspect
import json
import pytest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

from sim_sdk.context import SimContext, SimMode, set_context, clear_context
from sim_sdk.db import sim_db, SimWriteBlockedError, DBProxy, _is_write_statement, _compute_query_fingerprint
from sim_sdk.replay_context import ReplayContext


# ---------------------------------------------------------------------------
# FakeDB — test double with .query() and .execute() methods
# ---------------------------------------------------------------------------

class FakeDB:
    """A fake DB object with .query() and .execute() methods.

    Tracks all calls so tests can verify what was actually called.
    """

    def __init__(self):
        self.call_log: list = []
        self._results: dict = {}  # sql -> result mapping

    def set_result(self, sql: str, result):
        """Pre-configure what .query() should return for a given SQL."""
        self._results[sql] = result

    def query(self, sql: str, params=None):
        """Execute a query and return results."""
        self.call_log.append({"method": "query", "sql": sql, "params": params})
        return self._results.get(sql, [])

    def execute(self, sql: str, params=None):
        """Execute a statement (INSERT/UPDATE/DELETE)."""
        self.call_log.append({"method": "execute", "sql": sql, "params": params})
        return self._results.get(sql, None)

    def some_other_method(self):
        """A method that should be delegated through the proxy."""
        return "other_result"


class FakeDBExecuteOnly:
    """A fake DB with only .execute() — no .query() method."""

    def __init__(self):
        self.call_log: list = []

    def execute(self, sql: str, params=None):
        self.call_log.append({"method": "execute", "sql": sql, "params": params})
        return [{"id": 1}]


class FakeDBQueryOnly:
    """A fake DB with only .query() — no .execute() method."""

    def __init__(self):
        self.call_log: list = []

    def query(self, sql: str, params=None):
        self.call_log.append({"method": "query", "sql": sql, "params": params})
        return [{"name": "Alice"}]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_context():
    """Ensure each test starts with a clean context."""
    clear_context()
    yield
    clear_context()


@pytest.fixture
def stub_dir(tmp_path):
    """Provide a temporary stub directory."""
    return tmp_path / "stubs"


def make_record_ctx(stub_dir: Path, run_id: str = "test-run") -> SimContext:
    """Create a record-mode context."""
    stub_dir.mkdir(parents=True, exist_ok=True)
    ctx = SimContext(mode=SimMode.RECORD, run_id=run_id, stub_dir=stub_dir)
    set_context(ctx)
    return ctx


def make_replay_ctx(stub_dir: Path, run_id: str = "test-run") -> SimContext:
    """Create a replay-mode context."""
    ctx = SimContext(mode=SimMode.REPLAY, run_id=run_id, stub_dir=stub_dir)
    set_context(ctx)
    return ctx


def make_off_ctx() -> SimContext:
    """Create an off-mode context."""
    ctx = SimContext(mode=SimMode.OFF)
    set_context(ctx)
    return ctx


# ---------------------------------------------------------------------------
# ReplayContext helpers
# ---------------------------------------------------------------------------

@contextmanager
def replay_with_stubs(tmp_path, stubs):
    """Set up SimContext(REPLAY) + ReplayContext from a stubs list.

    Yields the SimContext so tests can inspect it if needed.
    """
    fixture_dir = tmp_path / "replay_fixtures"
    fixture_dir.mkdir(exist_ok=True)
    (fixture_dir / "test.json").write_text(
        json.dumps({"schema_version": 1, "stubs": stubs}), encoding="utf-8"
    )
    ctx = SimContext(mode=SimMode.REPLAY, run_id="test")
    set_context(ctx)
    replay_ctx = ReplayContext(fixture_id="test", fixture_dir=str(fixture_dir))
    with replay_ctx:
        yield ctx


def _db_stub(sql, params, output, ordinal=0, name="pg"):
    """Build a fixture stub entry matching what StubStore expects."""
    sql_fp, params_fp = _compute_query_fingerprint(sql, params)
    return {
        "qualname": f"db:{name}",
        "input_fingerprint": f"{sql_fp[:16]}:{params_fp[:16]}",
        "output": output,
        "ordinal": ordinal,
        "event_type": "Stub",
    }


# ===========================================================================
# 1. Record Mode
# ===========================================================================

class TestRecordMode:
    """Record mode: queries execute via underlying DB object, results captured."""

    def test_query_executes_on_real_db(self, stub_dir):
        """In record mode, .query() calls the real DB object."""
        make_record_ctx(stub_dir)
        fake = FakeDB()
        fake.set_result("SELECT 1", [{"one": 1}])

        with sim_db(fake, name="pg") as sdb:
            result = sdb.query("SELECT 1")

        assert result == [{"one": 1}]
        assert len(fake.call_log) == 1
        assert fake.call_log[0]["sql"] == "SELECT 1"

    def test_execute_calls_real_db(self, stub_dir):
        """In record mode, .execute() calls the real DB object."""
        make_record_ctx(stub_dir)
        fake = FakeDB()
        fake.set_result("INSERT INTO t VALUES (1)", {"affected": 1})

        with sim_db(fake) as sdb:
            result = sdb.execute("INSERT INTO t VALUES (1)")

        assert result == {"affected": 1}
        assert len(fake.call_log) == 1

    def test_fixture_written_to_disk(self, stub_dir):
        """Record mode writes a fixture JSON under __db__/."""
        make_record_ctx(stub_dir)
        fake = FakeDB()
        fake.set_result("SELECT * FROM users", [{"id": 1, "name": "Alice"}])

        with sim_db(fake, name="mydb") as sdb:
            sdb.query("SELECT * FROM users")

        # Check that a fixture file was created
        db_dir = stub_dir / "__db__"
        assert db_dir.exists()
        files = list(db_dir.iterdir())
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["type"] == "db_query"
        assert data["name"] == "mydb"
        assert data["sql"] == "SELECT * FROM users"
        assert data["result"] == [{"id": 1, "name": "Alice"}]

    def test_params_recorded(self, stub_dir):
        """Parameters are saved in the fixture."""
        make_record_ctx(stub_dir)
        fake = FakeDB()
        fake.set_result("SELECT * FROM users WHERE id = $1", [{"id": 42}])

        with sim_db(fake, name="pg") as sdb:
            sdb.query("SELECT * FROM users WHERE id = $1", [42])

        db_dir = stub_dir / "__db__"
        files = list(db_dir.iterdir())
        data = json.loads(files[0].read_text())
        assert data["params"] == [42]

    def test_stubs_collected(self, stub_dir):
        """Record mode pushes query to ctx.collected_stubs."""
        ctx = make_record_ctx(stub_dir)
        fake = FakeDB()
        fake.set_result("SELECT 1", [{"one": 1}])

        with sim_db(fake, name="pg") as sdb:
            sdb.query("SELECT 1")

        assert len(ctx.collected_stubs) == 1
        stub = ctx.collected_stubs[0]
        assert stub["type"] == "db_query"
        assert stub["name"] == "pg"
        assert stub["source"] == "record"


# ===========================================================================
# 2. Replay Mode
# ===========================================================================

class TestReplayMode:
    """Replay mode: queries return recorded rows from ReplayContext, real DB not called."""

    def test_replay_returns_recorded_rows(self, tmp_path):
        """Replay returns the rows stored in the StubStore fixture."""
        rows = [{"id": 1, "name": "Alice"}]
        stubs = [_db_stub("SELECT * FROM users", None, rows)]

        with replay_with_stubs(tmp_path, stubs):
            with sim_db(FakeDB(), name="pg") as sdb:
                result = sdb.query("SELECT * FROM users")

        assert result == rows

    def test_replay_does_not_call_real_db(self, tmp_path):
        """In replay mode, the real DB object is never called."""
        fake = FakeDB()
        stubs = [_db_stub("SELECT * FROM users", None, [{"id": 1}])]

        with replay_with_stubs(tmp_path, stubs):
            with sim_db(fake, name="pg") as sdb:
                sdb.query("SELECT * FROM users")

        assert len(fake.call_log) == 0

    def test_replay_with_params(self, tmp_path):
        """Parameterized queries are looked up by fingerprint including params."""
        rows = [{"id": 42, "name": "Eve"}]
        stubs = [_db_stub("SELECT * FROM users WHERE id = $1", [42], rows)]

        with replay_with_stubs(tmp_path, stubs):
            with sim_db(FakeDB(), name="pg") as sdb:
                result = sdb.query("SELECT * FROM users WHERE id = $1", [42])

        assert result == rows


# ===========================================================================
# 3. Replay Stub Miss — soft fail
# ===========================================================================

class TestReplayStubMiss:
    """Stub miss returns [] with a warning log; never raises."""

    def test_missing_fingerprint_returns_empty(self, tmp_path):
        """Replay with no matching stub returns [] instead of raising."""
        with replay_with_stubs(tmp_path, []):
            with sim_db(FakeDB(), name="pg") as sdb:
                result = sdb.query("SELECT * FROM nonexistent_table")

        assert result == []

    def test_different_params_returns_empty(self, tmp_path):
        """Same SQL with different params produces a miss that returns []."""
        stubs = [_db_stub("SELECT * FROM users WHERE id = $1", [1], [{"id": 1}])]

        with replay_with_stubs(tmp_path, stubs):
            with sim_db(FakeDB(), name="pg") as sdb:
                result = sdb.query("SELECT * FROM users WHERE id = $1", [999])

        assert result == []

    def test_no_replay_context_returns_empty(self):
        """Replay with SimContext but no active ReplayContext returns []."""
        ctx = SimContext(mode=SimMode.REPLAY, run_id="test")
        set_context(ctx)

        with sim_db(FakeDB()) as sdb:
            result = sdb.query("SELECT 1")

        assert result == []

    def test_miss_does_not_raise(self, tmp_path):
        """A stub miss must never propagate an exception."""
        with replay_with_stubs(tmp_path, []):
            with sim_db(FakeDB(), name="pg") as sdb:
                result = sdb.query("SELECT * FROM users")
        assert result == []


# ===========================================================================
# 4. Write Blocked
# ===========================================================================

class TestWriteBlocked:
    """SimWriteBlockedError on write statements in replay mode."""

    def test_insert_blocked(self, stub_dir):
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)
        fake = FakeDB()

        with pytest.raises(SimWriteBlockedError):
            with sim_db(fake) as sdb:
                sdb.query("INSERT INTO users (name) VALUES ('Alice')")

    def test_update_blocked(self, stub_dir):
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)
        fake = FakeDB()

        with pytest.raises(SimWriteBlockedError):
            with sim_db(fake) as sdb:
                sdb.execute("UPDATE users SET active = true WHERE id = 1")

    def test_delete_blocked(self, stub_dir):
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)
        fake = FakeDB()

        with pytest.raises(SimWriteBlockedError):
            with sim_db(fake) as sdb:
                sdb.query("DELETE FROM users WHERE id = 1")

    def test_drop_blocked(self, stub_dir):
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)
        fake = FakeDB()

        with pytest.raises(SimWriteBlockedError):
            with sim_db(fake) as sdb:
                sdb.query("DROP TABLE users")

    def test_truncate_blocked(self, stub_dir):
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)
        fake = FakeDB()

        with pytest.raises(SimWriteBlockedError):
            with sim_db(fake) as sdb:
                sdb.query("TRUNCATE users")

    def test_alter_blocked(self, stub_dir):
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)
        fake = FakeDB()

        with pytest.raises(SimWriteBlockedError):
            with sim_db(fake) as sdb:
                sdb.query("ALTER TABLE users ADD COLUMN age INT")

    def test_case_insensitive(self, stub_dir):
        """Write detection is case-insensitive."""
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)
        fake = FakeDB()

        with pytest.raises(SimWriteBlockedError):
            with sim_db(fake) as sdb:
                sdb.query("insert into users values (1)")

    def test_error_contains_sql(self, stub_dir):
        """SimWriteBlockedError includes the SQL statement."""
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)
        fake = FakeDB()

        with pytest.raises(SimWriteBlockedError) as exc_info:
            with sim_db(fake, name="mydb") as sdb:
                sdb.query("INSERT INTO users (name) VALUES ('test')")

        assert "INSERT INTO users" in str(exc_info.value)
        assert exc_info.value.name == "mydb"

    def test_select_not_blocked(self, tmp_path):
        """SELECT statements are NOT blocked in replay."""
        stubs = [_db_stub("SELECT 1", None, [{"one": 1}])]

        with replay_with_stubs(tmp_path, stubs):
            with sim_db(FakeDB(), name="pg") as sdb:
                result = sdb.query("SELECT 1")

        assert result == [{"one": 1}]

    def test_writes_allowed_in_record(self, stub_dir):
        """Write statements execute normally in record mode."""
        make_record_ctx(stub_dir)
        fake = FakeDB()

        with sim_db(fake) as sdb:
            sdb.execute("INSERT INTO users (name) VALUES ('Alice')")

        assert len(fake.call_log) == 1


# ===========================================================================
# 5. Ordinal Tracking
# ===========================================================================

class TestOrdinalTracking:
    """Repeated identical queries get distinct ordinals."""

    def test_same_query_increments_ordinal(self, stub_dir):
        """Two identical queries get ordinal 0 and 1."""
        make_record_ctx(stub_dir)
        fake = FakeDB()
        fake.set_result("SELECT 1", [{"one": 1}])

        with sim_db(fake, name="pg") as sdb:
            sdb.query("SELECT 1")
            sdb.query("SELECT 1")

        db_dir = stub_dir / "__db__"
        files = sorted(db_dir.iterdir())
        assert len(files) == 2

        data0 = json.loads(files[0].read_text())
        data1 = json.loads(files[1].read_text())
        assert data0["ordinal"] == 0
        assert data1["ordinal"] == 1

    def test_replay_respects_ordinals(self, tmp_path):
        """Same fingerprint called twice returns ordinal-0 then ordinal-1."""
        sql = "SELECT * FROM t WHERE id = $1"
        params = [1]
        stubs = [
            _db_stub(sql, params, [{"round": "first"}], ordinal=0),
            _db_stub(sql, params, [{"round": "second"}], ordinal=1),
        ]

        with replay_with_stubs(tmp_path, stubs):
            with sim_db(FakeDB(), name="pg") as sdb:
                r0 = sdb.query(sql, params)
                r1 = sdb.query(sql, params)

        assert r0 == [{"round": "first"}]
        assert r1 == [{"round": "second"}]


# ===========================================================================
# 6. Original Object Unmodified
# ===========================================================================

class TestOriginalUnmodified:
    """Outside the with block, original DB object is completely unmodified."""

    def test_no_attributes_added(self, stub_dir):
        """The original DB object has no new attributes after sim_db."""
        make_record_ctx(stub_dir)
        fake = FakeDB()
        attrs_before = set(dir(fake))

        with sim_db(fake) as sdb:
            sdb.query("SELECT 1")

        attrs_after = set(dir(fake))
        assert attrs_before == attrs_after

    def test_methods_unchanged(self, stub_dir):
        """Original methods still work normally after sim_db exits."""
        make_record_ctx(stub_dir)
        fake = FakeDB()
        fake.set_result("SELECT 1", [{"one": 1}])

        with sim_db(fake) as sdb:
            sdb.query("SELECT 1")

        # Call directly on original — should work fine
        result = fake.query("SELECT 1")
        assert result == [{"one": 1}]

    def test_proxy_is_different_object(self, stub_dir):
        """The yielded proxy is NOT the original object."""
        make_record_ctx(stub_dir)
        fake = FakeDB()

        with sim_db(fake) as sdb:
            assert sdb is not fake
            assert isinstance(sdb, DBProxy)


# ===========================================================================
# 7. Generic Interface
# ===========================================================================

class TestGenericInterface:
    """Works with any object that has .query() or .execute()."""

    def test_query_only_object(self, stub_dir):
        """Works with an object that only has .query()."""
        make_record_ctx(stub_dir)
        fake = FakeDBQueryOnly()

        with sim_db(fake, name="qonly") as sdb:
            result = sdb.query("SELECT name FROM users")

        assert result == [{"name": "Alice"}]
        assert len(fake.call_log) == 1

    def test_execute_only_object(self, stub_dir):
        """Works with an object that only has .execute()."""
        make_record_ctx(stub_dir)
        fake = FakeDBExecuteOnly()

        with sim_db(fake, name="eonly") as sdb:
            result = sdb.execute("SELECT id FROM t")

        assert result == [{"id": 1}]
        assert len(fake.call_log) == 1

    def test_other_methods_delegated(self, stub_dir):
        """Non-query methods are delegated to the underlying object."""
        make_record_ctx(stub_dir)
        fake = FakeDB()

        with sim_db(fake) as sdb:
            result = sdb.some_other_method()

        assert result == "other_result"


# ===========================================================================
# 8. Zero Framework Dependencies
# ===========================================================================

class TestZeroDependencies:
    """db.py must not import any DB driver."""

    def test_no_driver_imports(self):
        """Verify db.py source has no forbidden imports."""
        source = inspect.getsource(__import__("sim_sdk.db", fromlist=["db"]))

        forbidden = [
            "psycopg2", "psycopg", "pymysql", "mysql",
            "sqlite3", "sqlalchemy", "asyncpg", "aiomysql",
            "cx_Oracle", "pyodbc", "pymongo", "redis",
            "boto3",
        ]
        for lib in forbidden:
            assert f"import {lib}" not in source, f"db.py imports forbidden library: {lib}"
            assert f"from {lib}" not in source, f"db.py imports forbidden library: {lib}"


# ===========================================================================
# 9. Off Mode
# ===========================================================================

class TestOffMode:
    """Off mode: complete passthrough."""

    def test_off_mode_returns_original_object(self):
        """In off mode, sim_db yields the original DB object unwrapped."""
        make_off_ctx()
        fake = FakeDB()

        with sim_db(fake) as sdb:
            assert sdb is fake  # Same object, not a proxy

    def test_off_mode_query_works(self):
        """Queries work normally in off mode."""
        make_off_ctx()
        fake = FakeDB()
        fake.set_result("SELECT 1", [{"one": 1}])

        with sim_db(fake) as sdb:
            result = sdb.query("SELECT 1")

        assert result == [{"one": 1}]
        assert len(fake.call_log) == 1

    def test_off_mode_no_fixtures_created(self, stub_dir):
        """No fixture files are created in off mode."""
        make_off_ctx()
        fake = FakeDB()

        with sim_db(fake) as sdb:
            sdb.query("SELECT 1")

        assert not stub_dir.exists()


# ===========================================================================
# 10. Round-Trip via StubStore
# ===========================================================================

class TestRoundtrip:
    """Verify that replay returns the exact rows stored in the fixture."""

    def test_roundtrip_select(self, tmp_path):
        """SELECT result is returned unchanged from the StubStore."""
        rows = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        stubs = [_db_stub("SELECT * FROM users", None, rows)]

        fake = FakeDB()
        with replay_with_stubs(tmp_path, stubs):
            with sim_db(fake, name="pg") as sdb:
                result = sdb.query("SELECT * FROM users")

        assert result == rows
        assert len(fake.call_log) == 0  # real DB never called

    def test_roundtrip_with_params(self, tmp_path):
        """Parameterized query returns correct rows from StubStore."""
        rows = [{"id": 42, "name": "Eve"}]
        stubs = [_db_stub("SELECT * FROM users WHERE id = $1", [42], rows)]

        with replay_with_stubs(tmp_path, stubs):
            with sim_db(FakeDB(), name="pg") as sdb:
                result = sdb.query("SELECT * FROM users WHERE id = $1", [42])

        assert result == rows

    def test_roundtrip_empty_result(self, tmp_path):
        """Empty result set is returned as-is, not confused with a stub miss."""
        stubs = [_db_stub("SELECT * FROM empty_table", None, [])]

        with replay_with_stubs(tmp_path, stubs):
            with sim_db(FakeDB(), name="pg") as sdb:
                result = sdb.query("SELECT * FROM empty_table")

        assert result == []

    def test_roundtrip_null_output_treated_as_empty(self, tmp_path):
        """A fixture with output=null is normalized to [] on replay.

        None cannot serve as both a stored value and a miss sentinel in
        StubStore (dict.get() returns None for both), so null outputs are
        coerced to [] during indexing.  DB drivers that return None are
        unusual; treating them as empty-result is correct behaviour.
        """
        stubs = [_db_stub("SELECT * FROM t", None, None)]

        with replay_with_stubs(tmp_path, stubs):
            with sim_db(FakeDB(), name="pg") as sdb:
                result = sdb.query("SELECT * FROM t")

        assert result == []


# ===========================================================================
# Sink integration
# ===========================================================================

class TestSinkIntegration:
    """Record mode uses ctx.sink when available."""

    def test_record_emits_to_sink(self, stub_dir):
        """When ctx.sink is set, DB fixtures are emitted as FixtureEvents."""
        stub_dir.mkdir(parents=True, exist_ok=True)
        mock_sink = MagicMock()
        ctx = SimContext(
            mode=SimMode.RECORD, run_id="test", stub_dir=stub_dir, sink=mock_sink,
        )
        set_context(ctx)

        fake = FakeDB()
        fake.set_result("SELECT 1", [{"one": 1}])

        with sim_db(fake, name="pg") as sdb:
            sdb.query("SELECT 1")

        # Sink.emit() should have been called (not write())
        mock_sink.emit.assert_called_once()
        event = mock_sink.emit.call_args[0][0]
        assert event.storage_key.startswith("__db__/pg_")
        assert event.qualname == "db:pg"
        assert event.output == [{"one": 1}]
        assert event.input["sql"] == "SELECT 1"


# ===========================================================================
# Async context manager
# ===========================================================================

class TestAsyncContextManager:
    """sim_db works as an async context manager."""

    @pytest.mark.asyncio
    async def test_async_record(self, stub_dir):
        """Async record mode works."""
        make_record_ctx(stub_dir)
        fake = FakeDB()
        fake.set_result("SELECT 1", [{"one": 1}])

        async with sim_db(fake, name="pg") as sdb:
            result = sdb.query("SELECT 1")

        assert result == [{"one": 1}]

    @pytest.mark.asyncio
    async def test_async_off_mode(self):
        """Async off mode returns original object."""
        make_off_ctx()
        fake = FakeDB()

        async with sim_db(fake) as sdb:
            assert sdb is fake


# ===========================================================================
# Write detection helper
# ===========================================================================

class TestWriteDetection:
    """Tests for the _is_write_statement helper."""

    def test_select_is_not_write(self):
        assert _is_write_statement("SELECT * FROM t") is False

    def test_insert_is_write(self):
        assert _is_write_statement("INSERT INTO t VALUES (1)") is True

    def test_update_is_write(self):
        assert _is_write_statement("UPDATE t SET x = 1") is True

    def test_delete_is_write(self):
        assert _is_write_statement("DELETE FROM t") is True

    def test_drop_is_write(self):
        assert _is_write_statement("DROP TABLE t") is True

    def test_alter_is_write(self):
        assert _is_write_statement("ALTER TABLE t ADD COLUMN x INT") is True

    def test_truncate_is_write(self):
        assert _is_write_statement("TRUNCATE t") is True

    def test_case_insensitive(self):
        assert _is_write_statement("insert into t values (1)") is True
        assert _is_write_statement("select * from t") is False

    def test_leading_whitespace(self):
        assert _is_write_statement("  INSERT INTO t VALUES (1)") is True
        assert _is_write_statement("\n  SELECT 1") is False

    def test_with_cte_insert(self):
        sql = "WITH cte AS (SELECT 1) INSERT INTO t SELECT * FROM cte"
        assert _is_write_statement(sql) is True

    def test_with_cte_select(self):
        sql = "WITH cte AS (SELECT 1) SELECT * FROM cte"
        assert _is_write_statement(sql) is False
