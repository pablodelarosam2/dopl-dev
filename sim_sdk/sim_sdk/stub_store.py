"""
StubStore — in-memory replay data layer for sim_sdk fixture files.

Loads a fixture.json (schema_version >= 1) produced by @sim_trace, partitions
the top-level ``stubs`` array by qualname prefix, and builds three O(1) lookup
indexes:

    DB index    keyed by (input_fingerprint, ordinal)  → recorded rows
    HTTP index  keyed by (label, ordinal)              → (status, body, headers)
    Trace index keyed by (input_fingerprint, ordinal)  → full stub payload

Each entry in ``stubs`` is a FixtureEvent with ``event_type``, ``qualname``,
``input_fingerprint``, ``output``, and ``ordinal``.  The qualname prefix
determines the target index:

    "db:<name>"       → DB index,   key = (input_fingerprint, ordinal)
    "capture:<label>" → HTTP index, key = (label, ordinal)
    "http:<label>"    → HTTP index, key = (label, ordinal)
    other / no prefix → Trace index, key = (input_fingerprint, ordinal)

Only entries with ``event_type == "Stub"`` are indexed.  The top-level
``golden_output`` (``event_type == "Output"``) is not part of the stubs array
and is never indexed here.

Lookup methods return None on miss — adapters decide miss behavior.

Zero framework dependencies (Zone 1 compliant):
  imports: json, pathlib, typing
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DB_PREFIX = "db:"
_CAPTURE_PREFIX = "capture:"
_HTTP_PREFIX = "http:"


class StubStore:
    """In-memory index of stubs loaded from a single fixture.json file."""

    def __init__(self) -> None:
        # (input_fingerprint, ordinal) → List[Dict]
        self._db: Dict[Tuple[str, int], List[Dict]] = {}
        # (label, ordinal) → (status, body, headers)
        self._http: Dict[Tuple[str, int], Tuple[int, Dict, Dict]] = {}
        # (input_fingerprint, ordinal) → Dict
        self._trace: Dict[Tuple[str, int], Dict] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_fixture(cls, path: str) -> "StubStore":
        """Load and index a fixture.json file.

        Args:
            path: Filesystem path to the fixture JSON file.

        Returns:
            A fully populated StubStore ready for lookups.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
            ValueError: If the file is not valid JSON.
        """
        fixture_path = Path(path)
        if not fixture_path.exists():
            raise FileNotFoundError(f"Fixture not found: {path}")

        with open(fixture_path, "r", encoding="utf-8") as fh:
            try:
                data: Dict[str, Any] = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in fixture {path}: {exc}") from exc

        store = cls()
        store._index_stubs(data.get("stubs", []))

        # The top-level golden_output holds the recorded return value of the
        # root @sim_trace function.  Index it into _trace so get_trace_stub()
        # can serve it during replay.
        golden_output = data.get("golden_output")
        if golden_output is not None:
            store._index_fixture_event(golden_output)

        return store

    # ------------------------------------------------------------------
    # Internal indexing
    # ------------------------------------------------------------------

    def _index_stubs(self, stubs: List[Dict[str, Any]]) -> None:
        """Iterate the stubs array and dispatch each entry to its index."""
        for stub in stubs:
            self._index_fixture_event(stub)

    def _index_fixture_event(self, stub: Dict[str, Any]) -> None:
        """Route one FixtureEvent stub into the appropriate index."""
        qualname: str = stub.get("qualname", "")
        ordinal: int = stub.get("ordinal", 0)

        if qualname.startswith(_DB_PREFIX):
            fp: str = stub.get("input_fingerprint", "")
            output = stub.get("output", [])
            # Normalize null output to [] so None unambiguously signals a miss
            # in get_db_stub (which uses dict.get() returning None on miss).
            self._db[(fp, ordinal)] = output if output is not None else []

        elif qualname.startswith(_CAPTURE_PREFIX):
            label = qualname[len(_CAPTURE_PREFIX):]
            self._http[(label, ordinal)] = (
                stub.get("status", 200),
                stub.get("output", {}),
                stub.get("headers", {}),
            )

        elif qualname.startswith(_HTTP_PREFIX):
            label = qualname[len(_HTTP_PREFIX):]
            self._http[(label, ordinal)] = (
                stub.get("status", 200),
                stub.get("output", {}),
                stub.get("headers", {}),
            )

        else:
            # Nested @sim_trace or other trace events.
            fp = stub.get("input_fingerprint", "")
            self._trace[(fp, ordinal)] = stub

    # ------------------------------------------------------------------
    # Public lookup API — returns None on miss, adapters decide behavior
    # ------------------------------------------------------------------

    def get_db_stub(self, fp: str, ordinal: int) -> Optional[List[Dict]]:
        """Return recorded rows for a DB query, or None if not found.

        Args:
            fp: ``input_fingerprint`` from the FixtureEvent, formatted as
                ``"<sql_fp_16>:<params_fp_16>"``.
            ordinal: 0-based call ordinal for this fingerprint.

        Returns:
            List of row dicts as recorded, or None on miss.
        """
        return self._db.get((fp, ordinal))

    def get_http_stub(self, fp: str, ordinal: int) -> Optional[Tuple[int, Dict, Dict]]:
        """Return recorded HTTP/capture response, or None if not found.

        Args:
            fp: Label string extracted from ``qualname`` after the prefix
                (e.g. ``"tax_service"`` from ``"capture:tax_service"``).
            ordinal: 0-based call ordinal for this label.

        Returns:
            ``(status, body, headers)`` tuple as recorded, or None on miss.
        """
        return self._http.get((fp, ordinal))

    def get_trace_stub(self, fp: str, ordinal: int) -> Optional[Any]:
        """Return recorded internal trace payload, or None if not found.

        Args:
            fp: ``input_fingerprint`` from the FixtureEvent.
            ordinal: 0-based call ordinal for this fingerprint.

        Returns:
            Full FixtureEvent stub dict, or None on miss.
        """
        return self._trace.get((fp, ordinal))

    # ------------------------------------------------------------------
    # Available fingerprint inspection
    # ------------------------------------------------------------------

    def available_db_fingerprints(self) -> List[str]:
        """Return the unique fingerprints present in the DB index."""
        return list({fp for fp, _ in self._db})

    def available_http_fingerprints(self) -> List[str]:
        """Return the unique labels present in the HTTP/capture index."""
        return list({label for label, _ in self._http})

    def available_trace_fingerprints(self) -> List[str]:
        """Return the unique fingerprints present in the trace index."""
        return list({fp for fp, _ in self._trace})
