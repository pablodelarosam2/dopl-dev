"""
Database adapter for psycopg2 with record/replay support.

Provides a wrapper around psycopg2 connections that:
- Record mode: Execute queries and save results to stub store
- Replay mode: Return recorded results, block writes
"""

import re
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import connection as PgConnection
from psycopg2.extensions import cursor as PgCursor
from psycopg2.extras import RealDictCursor

from sim_sdk.canonicalize import fingerprint_sql
from sim_sdk.context import get_context
from sim_sdk.store import StubStore
from sim_sdk.trace import add_db_stub


class SimWriteBlocked(Exception):
    """Raised when a write operation is attempted in replay mode."""

    def __init__(self, sql: str):
        self.sql = sql
        super().__init__(
            f"Write operations are blocked in replay mode. SQL: {sql[:100]}..."
        )


class SimStubMissError(Exception):
    """Raised when a stub is not found for a query in replay mode."""

    def __init__(self, fingerprint: str, sql: str):
        self.fingerprint = fingerprint
        self.sql = sql
        super().__init__(
            f"No stub found for query (fingerprint: {fingerprint}). SQL: {sql[:100]}..."
        )


# Regex patterns to detect write operations
WRITE_PATTERNS = [
    re.compile(r"^\s*INSERT\s+", re.IGNORECASE),
    re.compile(r"^\s*UPDATE\s+", re.IGNORECASE),
    re.compile(r"^\s*DELETE\s+", re.IGNORECASE),
    re.compile(r"^\s*DROP\s+", re.IGNORECASE),
    re.compile(r"^\s*CREATE\s+", re.IGNORECASE),
    re.compile(r"^\s*ALTER\s+", re.IGNORECASE),
    re.compile(r"^\s*TRUNCATE\s+", re.IGNORECASE),
]


def _is_write_operation(sql_str: str) -> bool:
    """Check if a SQL statement is a write operation."""
    for pattern in WRITE_PATTERNS:
        if pattern.match(sql_str):
            return True
    return False


class SimDB:
    """
    Database wrapper with simulation support.

    Provides a simple interface for database operations that integrates
    with the simulation context for record/replay.

    Example:
        db = SimDB(dsn="postgresql://localhost/mydb")

        # Query (works in all modes)
        rows = db.query("SELECT * FROM users WHERE id = %s", (user_id,))

        # Execute (blocked in replay mode)
        db.execute("UPDATE users SET name = %s WHERE id = %s", (name, user_id))

        # Transaction
        with db.transaction():
            db.execute(...)
            db.execute(...)
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        connection: Optional[PgConnection] = None,
        **connect_kwargs: Any,
    ):
        """
        Initialize the database wrapper.

        Args:
            dsn: Database connection string
            connection: Existing psycopg2 connection (optional)
            **connect_kwargs: Additional arguments for psycopg2.connect()
        """
        self._dsn = dsn
        self._connect_kwargs = connect_kwargs
        self._connection: Optional[PgConnection] = connection
        self._in_transaction = False

    def _get_connection(self) -> PgConnection:
        """Get or create a database connection."""
        ctx = get_context()

        # In replay mode, we don't need a real connection
        if ctx.is_replaying:
            return None

        if self._connection is None or self._connection.closed:
            if self._dsn:
                self._connection = psycopg2.connect(self._dsn, **self._connect_kwargs)
            else:
                self._connection = psycopg2.connect(**self._connect_kwargs)

        return self._connection

    def query(
        self,
        sql_str: str,
        params: Union[Tuple, List, Dict, None] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a SELECT query and return results.

        Args:
            sql_str: SQL query string
            params: Query parameters

        Returns:
            List of row dictionaries

        Raises:
            SimStubMissError: In replay mode if no stub is found
        """
        ctx = get_context()

        # Generate fingerprint
        fp = fingerprint_sql(sql_str, params)
        ordinal = ctx.next_ordinal(f"db:{fp}")

        # Replay mode: return from stub store
        if ctx.is_replaying:
            return self._replay_query(fp, ordinal, sql_str, ctx)

        # Execute real query
        conn = self._get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql_str, params)
            rows = [dict(row) for row in cur.fetchall()]

        # Record mode: save results
        if ctx.is_recording and ctx.stub_dir:
            self._record_query(fp, ordinal, sql_str, params, rows, ctx)

        return rows

    def query_one(
        self,
        sql_str: str,
        params: Union[Tuple, List, Dict, None] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute a SELECT query and return the first row.

        Args:
            sql_str: SQL query string
            params: Query parameters

        Returns:
            First row as dictionary, or None if no results
        """
        rows = self.query(sql_str, params)
        return rows[0] if rows else None

    def execute(
        self,
        sql_str: str,
        params: Union[Tuple, List, Dict, None] = None,
    ) -> int:
        """
        Execute a write operation (INSERT, UPDATE, DELETE).

        Args:
            sql_str: SQL statement
            params: Statement parameters

        Returns:
            Number of affected rows

        Raises:
            SimWriteBlocked: In replay mode
        """
        ctx = get_context()

        # Block writes in replay mode
        if ctx.is_replaying:
            if _is_write_operation(sql_str):
                raise SimWriteBlocked(sql_str)
            # If it's not a write operation, treat as query
            self.query(sql_str, params)
            return 0

        # Execute real statement
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(sql_str, params)
            rowcount = cur.rowcount

        # Auto-commit if not in transaction
        if not self._in_transaction:
            conn.commit()

        return rowcount

    def executemany(
        self,
        sql_str: str,
        params_list: List[Union[Tuple, List, Dict]],
    ) -> int:
        """
        Execute a statement with multiple parameter sets.

        Args:
            sql_str: SQL statement
            params_list: List of parameter sets

        Returns:
            Total number of affected rows

        Raises:
            SimWriteBlocked: In replay mode
        """
        ctx = get_context()

        if ctx.is_replaying and _is_write_operation(sql_str):
            raise SimWriteBlocked(sql_str)

        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.executemany(sql_str, params_list)
            rowcount = cur.rowcount

        if not self._in_transaction:
            conn.commit()

        return rowcount

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """
        Context manager for database transactions.

        Example:
            with db.transaction():
                db.execute("INSERT INTO ...")
                db.execute("UPDATE ...")
        """
        ctx = get_context()

        # In replay mode, transactions are no-ops
        if ctx.is_replaying:
            yield
            return

        conn = self._get_connection()
        self._in_transaction = True

        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._in_transaction = False

    def _replay_query(
        self,
        fp: str,
        ordinal: int,
        sql_str: str,
        ctx,
    ) -> List[Dict[str, Any]]:
        """Load query results from stub store."""
        if ctx.stub_dir is None:
            raise SimStubMissError(fp, sql_str)

        store = StubStore(ctx.stub_dir)

        # Try with ordinal first
        rows = store.load_db(fp, ordinal)
        if rows is None:
            # Try without ordinal (ordinal 0)
            rows = store.load_db(fp, 0)

        if rows is None:
            raise SimStubMissError(fp, sql_str)

        return rows

    def _record_query(
        self,
        fp: str,
        ordinal: int,
        sql_str: str,
        params: Any,
        rows: List[Dict[str, Any]],
        ctx,
    ) -> None:
        """Save query results to stub store."""
        store = StubStore(ctx.stub_dir)

        # Convert any non-JSON-serializable types
        serializable_rows = _make_serializable(rows)

        store.save_db(
            fp,
            ordinal,
            serializable_rows,
            metadata={
                "sql": sql_str,
                "params": _make_serializable(params),
                "row_count": len(rows),
            },
        )

        # Also add to trace collector for @sim_trace decorator support
        add_db_stub({
            "fingerprint": fp,
            "ordinal": ordinal,
            "rows": serializable_rows,
            "sql": sql_str,
            "params": _make_serializable(params),
        })

    def close(self) -> None:
        """Close the database connection."""
        if self._connection and not self._connection.closed:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "SimDB":
        """Context manager entry."""
        return self

    def __exit__(self, *args) -> None:
        """Context manager exit."""
        self.close()


def _make_serializable(value: Any) -> Any:
    """
    Convert a value to a JSON-serializable form.
    """
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        return value.hex()

    if isinstance(value, dict):
        return {k: _make_serializable(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_make_serializable(item) for item in value]

    # Handle datetime, date, time, etc.
    if hasattr(value, "isoformat"):
        return value.isoformat()

    # Handle Decimal
    if hasattr(value, "__float__"):
        return float(value)

    # Fallback to string
    return str(value)
