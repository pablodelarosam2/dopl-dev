"""
ReplayContext — per-request context manager for deterministic fixture replay.

Loads a StubStore from a fixture file, maintains separate ordinal counters for
DB, HTTP, and trace call types, and stores itself in a ContextVar so adapters
can retrieve it without explicit argument threading.

Usage (middleware):

    with ReplayContext(fixture_id="calculate_quote", fixture_dir="/app/.sim/fixtures"):
        # adapters call get_replay_context() internally
        response = handle_request(request)

Usage (explicit, e.g. async frameworks):

    token = set_replay_context(ctx)
    try:
        response = await handle_request(request)
    finally:
        clear_replay_context(token)

Zone 1 compliant — stdlib only:
  imports: collections, contextvars, logging, pathlib, typing
"""

import logging
from collections import defaultdict
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Dict, Optional

from .stub_store import StubStore

_log = logging.getLogger(__name__)

_sim_replay_context: ContextVar[Optional["ReplayContext"]] = ContextVar(
    "sim_replay_context", default=None
)


class ReplayContext:
    """
    Per-request replay state: loaded StubStore and per-type ordinal counters.

    Maintains three independent ordinal sequences (db / http / trace) so that
    calls of different types do not interfere with each other's ordinal counts.
    All counters are 0-based and match the ordinals stored in fixture files.

    Args:
        fixture_id: Logical name of the fixture (e.g. "calculate_quote").
            The file ``<fixture_dir>/<fixture_id>.json`` must exist.
        fixture_dir: Directory that contains fixture JSON files.

    Raises:
        FileNotFoundError: If the resolved fixture path does not exist.
        ValueError: If the fixture file is not valid JSON.
    """

    def __init__(self, fixture_id: str, fixture_dir: str) -> None:
        self.fixture_id = fixture_id
        path = Path(fixture_dir) / f"{fixture_id}.json"
        self.stub_store: StubStore = StubStore.from_fixture(str(path))
        self.db_ordinals: Dict[str, int] = defaultdict(int)
        self.http_ordinals: Dict[str, int] = defaultdict(int)
        self.trace_ordinals: Dict[str, int] = defaultdict(int)
        self._token: Optional[Token] = None

    # ------------------------------------------------------------------
    # Ordinal counters — 0-based, one sequence per call type
    # ------------------------------------------------------------------

    def next_db_ordinal(self, fingerprint: str) -> int:
        """Return the next 0-based ordinal for a DB query fingerprint."""
        current = self.db_ordinals[fingerprint]
        self.db_ordinals[fingerprint] = current + 1
        return current

    def next_http_ordinal(self, fingerprint: str) -> int:
        """Return the next 0-based ordinal for an HTTP/capture fingerprint."""
        current = self.http_ordinals[fingerprint]
        self.http_ordinals[fingerprint] = current + 1
        return current

    def next_trace_ordinal(self, fingerprint: str) -> int:
        """Return the next 0-based ordinal for an internal @sim_trace fingerprint."""
        current = self.trace_ordinals[fingerprint]
        self.trace_ordinals[fingerprint] = current + 1
        return current

    # ------------------------------------------------------------------
    # Context manager — lifecycle management
    # ------------------------------------------------------------------

    def __enter__(self) -> "ReplayContext":
        self._token = _sim_replay_context.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._log_unused_stubs()
        if self._token is not None:
            _sim_replay_context.reset(self._token)
            self._token = None

    def _log_unused_stubs(self) -> None:
        """Emit debug-level warnings for stubs that were never consumed."""
        store = self.stub_store

        for fp in store.available_db_fingerprints():
            if self.db_ordinals.get(fp, 0) == 0:
                _log.debug(
                    "Unused db stub: fixture_id=%s fingerprint=%s",
                    self.fixture_id, fp,
                )

        for label in store.available_http_fingerprints():
            if self.http_ordinals.get(label, 0) == 0:
                _log.debug(
                    "Unused http stub: fixture_id=%s label=%s",
                    self.fixture_id, label,
                )

        for fp in store.available_trace_fingerprints():
            if self.trace_ordinals.get(fp, 0) == 0:
                _log.debug(
                    "Unused trace stub: fixture_id=%s fingerprint=%s",
                    self.fixture_id, fp,
                )


# ---------------------------------------------------------------------------
# Public helpers — called by adapters and middleware
# ---------------------------------------------------------------------------

def get_replay_context() -> Optional[ReplayContext]:
    """Return the active ReplayContext for the current thread/task, or None."""
    return _sim_replay_context.get()


def set_replay_context(ctx: ReplayContext) -> Token:
    """Set a ReplayContext for the current thread/task.

    Returns a Token that must be passed to clear_replay_context() to restore
    the previous state.  Prefer the context manager form when possible.
    """
    return _sim_replay_context.set(ctx)


def clear_replay_context(token: Token) -> None:
    """Restore the ContextVar to the state before the matching set_replay_context()."""
    _sim_replay_context.reset(token)
