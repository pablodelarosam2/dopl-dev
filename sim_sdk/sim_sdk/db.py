"""
sim_db() context manager for database query capture.

Wraps ANY object that has a callable .query() or .execute() method.
Does NOT import any database driver or ORM library.

Record mode: queries execute via underlying DB object, results captured.
Replay mode: queries return recorded rows, underlying DB object not called.
Off mode: complete passthrough, zero overhead.

Fingerprint = normalize_sql(sql) + fingerprint(params) + ordinal.
Write detection: INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE → SimWriteBlockedError in replay.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .context import SimContext, SimMode, get_context
from .canonical import normalize_sql, fingerprint, fingerprint_sql
from .trace import SimStubMissError, _make_serializable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SimWriteBlockedError(Exception):
    """Raised when a write statement (INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE)
    is attempted in replay mode.

    Attributes:
        sql: The SQL statement that was blocked.
        name: The sim_db connection name.
    """

    def __init__(self, sql: str, name: str = "db"):
        self.sql = sql
        self.name = name
        # Show first 80 chars of SQL for readability
        sql_preview = sql[:80] + ("..." if len(sql) > 80 else "")
        super().__init__(
            f"Write statement blocked in replay mode [{name}]: {sql_preview}"
        )


# ---------------------------------------------------------------------------
# Write detection
# ---------------------------------------------------------------------------

_WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE")


def _is_write_statement(sql: str) -> bool:
    """Check if a SQL statement is a write operation."""
    normalized = sql.strip().upper()
    # Handle common prefixes like WITH ... INSERT
    # Strip leading WITH clause for CTE detection
    if normalized.startswith("WITH"):
        # Find the actual DML after the CTE
        # Look for the first top-level INSERT/UPDATE/DELETE
        for prefix in _WRITE_PREFIXES:
            if prefix in normalized:
                return True
        return False
    return normalized.startswith(_WRITE_PREFIXES)


# ---------------------------------------------------------------------------
# Fixture I/O
# ---------------------------------------------------------------------------

def _db_fixture_key(name: str, sql_fp: str, params_fp: str, ordinal: int) -> str:
    """Build the relative path key for a DB query fixture file.

    Layout: __db__/{safe_name}_{sql_fp[:8]}_{params_fp[:8]}_{ordinal}.json
    """
    safe_name = name.replace(".", "_").replace("/", "_").replace(" ", "_")
    return f"__db__/{safe_name}_{sql_fp[:8]}_{params_fp[:8]}_{ordinal}.json"


def _compute_query_fingerprint(sql: str, params: Any) -> Tuple[str, str]:
    """Compute fingerprints for a SQL query and its parameters.

    Returns:
        Tuple of (sql_fingerprint, params_fingerprint)
    """
    sql_fp = fingerprint_sql(sql)
    params_data = _make_serializable(params) if params is not None else None
    params_fp = fingerprint(params_data) if params_data is not None else fingerprint("")
    return sql_fp, params_fp


def _write_db_fixture(
    name: str,
    sql: str,
    params: Any,
    sql_fp: str,
    params_fp: str,
    ordinal: int,
    result: Any,
    ctx: SimContext,
) -> None:
    """Persist a DB query fixture to sink or stub_dir."""
    data = {
        "type": "db_query",
        "name": name,
        "sql": sql,
        "params": _make_serializable(params),
        "sql_fingerprint": sql_fp,
        "params_fingerprint": params_fp,
        "ordinal": ordinal,
        "result": _make_serializable(result),
    }

    key = _db_fixture_key(name, sql_fp, params_fp, ordinal)

    if ctx.sink is not None:
        ctx.sink.write(key, data)
        return

    if ctx.stub_dir is not None:
        filepath = ctx.stub_dir / key
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return

    logger.debug("No sink or stub_dir — db fixture %r discarded", name)


def _read_db_fixture(
    name: str,
    sql_fp: str,
    params_fp: str,
    ordinal: int,
    stub_dir: Path,
) -> Optional[Dict[str, Any]]:
    """Read a recorded DB query fixture from stub_dir."""
    key = _db_fixture_key(name, sql_fp, params_fp, ordinal)
    filepath = stub_dir / key
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------------------
# DBProxy — intercepts .query() and .execute() calls
# ---------------------------------------------------------------------------

class DBProxy:
    """Transparent proxy that intercepts query/execute calls on a DB object.

    In record mode, calls the real method and captures the result.
    In replay mode, returns recorded results without calling the real method.
    Delegates all other attribute access to the underlying object.
    """

    def __init__(self, db_object: Any, name: str, ctx: SimContext):
        # Use object.__setattr__ to avoid triggering our __setattr__ if defined
        object.__setattr__(self, "_db_object", db_object)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_queries_captured", [])

    def __getattr__(self, attr: str) -> Any:
        """Intercept 'query'/'execute'; delegate everything else to the real DB object.

        Only __getattr__ (not __getattribute__) is used, so our own _fields
        (set via object.__setattr__ in __init__) resolve normally without
        triggering this method.
        """
        if attr in ("query", "execute"):
            return self._make_interceptor(attr)
        return getattr(object.__getattribute__(self, "_db_object"), attr)

    def _make_interceptor(self, method_name: str):
        """Return a closure that routes query/execute through _intercept_call."""
        def interceptor(sql: str, params: Any = None, *args: Any, **kwargs: Any) -> Any:
            return self._intercept_call(method_name, sql, params, *args, **kwargs)
        return interceptor

    def _intercept_call(
        self, method_name: str, sql: str, params: Any = None,
        *args: Any, **kwargs: Any,
    ) -> Any:
        """Route a DB call to replay, record, or passthrough based on mode."""
        ctx = object.__getattribute__(self, "_ctx")
        name = object.__getattribute__(self, "_name")
        db_object = object.__getattribute__(self, "_db_object")

        # Compute fingerprints
        sql_fp, params_fp = _compute_query_fingerprint(sql, params)
        combined_fp = f"db:{name}:{sql_fp[:16]}:{params_fp[:16]}"
        ordinal = ctx.next_ordinal(combined_fp)

        if ctx.is_replaying:
            return self._replay_call(sql, params, sql_fp, params_fp, ordinal, name, ctx)

        if ctx.is_recording:
            return self._record_call(
                method_name, sql, params, sql_fp, params_fp, ordinal,
                name, db_object, ctx, *args, **kwargs,
            )

        # Should not reach here (off mode is handled by sim_db yielding raw object)
        real_method = getattr(db_object, method_name)
        if params is not None:
            return real_method(sql, params, *args, **kwargs)
        return real_method(sql, *args, **kwargs)

    def _replay_call(
        self, sql: str, params: Any, sql_fp: str, params_fp: str,
        ordinal: int, name: str, ctx: SimContext,
    ) -> Any:
        """Handle a DB call in replay mode."""
        # Block writes in replay
        if _is_write_statement(sql):
            raise SimWriteBlockedError(sql, name)

        if ctx.stub_dir is None:
            raise SimStubMissError(f"db:{name}", f"{sql_fp[:16]}:{params_fp[:16]}", ordinal)

        fixture = _read_db_fixture(name, sql_fp, params_fp, ordinal, ctx.stub_dir)
        if fixture is None:
            raise SimStubMissError(
                f"db:{name}", f"{sql_fp[:16]}:{params_fp[:16]}", ordinal, ctx.stub_dir,
            )

        result = fixture.get("result")

        # Push to collected_stubs for outer @sim_trace
        ctx.collected_stubs.append({
            "type": "db_query",
            "name": name,
            "sql": sql,
            "ordinal": ordinal,
            "result": result,
            "source": "replay",
        })

        return result

    def _record_call(
        self, method_name: str, sql: str, params: Any,
        sql_fp: str, params_fp: str, ordinal: int, name: str,
        db_object: Any, ctx: SimContext, *args: Any, **kwargs: Any,
    ) -> Any:
        """Handle a DB call in record mode."""
        # Execute the real query
        real_method = getattr(db_object, method_name)
        if params is not None:
            result = real_method(sql, params, *args, **kwargs)
        else:
            result = real_method(sql, *args, **kwargs)

        # Write fixture
        _write_db_fixture(name, sql, params, sql_fp, params_fp, ordinal, result, ctx)

        # Push to collected_stubs for outer @sim_trace
        ctx.collected_stubs.append({
            "type": "db_query",
            "name": name,
            "sql": sql,
            "ordinal": ordinal,
            "result": _make_serializable(result),
            "source": "record",
        })

        # Track locally
        queries = object.__getattribute__(self, "_queries_captured")
        queries.append({"sql": sql, "params": params, "ordinal": ordinal})

        return result


# ---------------------------------------------------------------------------
# Public API — sim_db context manager
# ---------------------------------------------------------------------------

class sim_db:
    """Context manager that wraps a DB object to intercept query/execute calls.

    Record mode::

        with sim_db(db_conn, name="postgres") as sdb:
            rows = sdb.query("SELECT * FROM users WHERE id = $1", [42])

    Replay mode::

        with sim_db(db_conn, name="postgres") as sdb:
            rows = sdb.query("SELECT * FROM users WHERE id = $1", [42])
            # returns recorded rows, db_conn.query() never called

    Off mode: yields the original db_conn unwrapped.

    Args:
        db_object: Any object with .query() or .execute() methods.
        name: Label for this DB connection (used in fixture file paths).
    """

    def __init__(self, db_object: Any, name: str = "db"):
        self._db_object = db_object
        self._name = name
        self._proxy: Optional[DBProxy] = None
        self._ctx: Optional[SimContext] = None

    def _setup(self) -> Any:
        """Common setup for both sync and async entry."""
        self._ctx = get_context()

        if not self._ctx.is_active:
            # Off mode — return original object unwrapped
            return self._db_object

        self._proxy = DBProxy(self._db_object, self._name, self._ctx)
        return self._proxy

    def _teardown(self) -> None:
        """Common teardown for both sync and async exit."""
        # Nothing to clean up — stubs are pushed per-query in DBProxy
        pass

    # -- Sync context manager -----------------------------------------------

    def __enter__(self) -> Any:
        return self._setup()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._teardown()

    # -- Async context manager ----------------------------------------------

    async def __aenter__(self) -> Any:
        return self._setup()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._teardown()
