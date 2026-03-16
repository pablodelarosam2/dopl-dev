"""
Shared exception classes for the sim_sdk replay system.

Centralised here to avoid circular imports between trace.py, db.py,
capture.py, and stub_store.py.
"""

from typing import List


class SimStubMissError(Exception):
    """Raised when replay lookup fails to find a matching recorded stub.

    Attributes:
        stub_type: Category of the missing stub — "db", "http", or "trace".
        fingerprint: Fingerprint string used for the lookup.
        ordinal: 0-based call ordinal used for the lookup.
        available: Deduplicated list of known fingerprints in the index,
            for diagnostic output.  Empty list when the caller has no index
            to introspect (e.g. raw file-based replay in trace.py / db.py).
    """

    def __init__(
        self,
        stub_type: str,
        fingerprint: str,
        ordinal: int,
        available: List[str],
    ) -> None:
        self.stub_type = stub_type
        self.fingerprint = fingerprint
        self.ordinal = ordinal
        self.available = available

        fp_preview = fingerprint[:16] if fingerprint else "<empty>"
        msg = (
            f"No recorded {stub_type} stub "
            f"(fingerprint={fp_preview!r}, ordinal={ordinal})."
        )
        if available:
            msg += f" Known fingerprints: {available}"

        super().__init__(msg)
