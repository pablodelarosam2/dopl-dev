"""
sim_sdk - Simulation SDK for deterministic capture and replay

This package provides tools for instrumenting Flask applications to:
- Capture request/response data
- Record and replay HTTP calls
- Record and replay database queries
- Ensure deterministic execution for testing
"""

from sim_sdk.context import (
    SimContext,
    get_context,
    set_context,
    clear_context,
    SimMode,
)
from sim_sdk.canonicalize import canonicalize, fingerprint
from sim_sdk.redaction import redact, DEFAULT_REDACT_PATHS
from sim_sdk.store import StubStore
from sim_sdk.clock import SimClock, sim_clock
from sim_sdk.flask_middleware import sim_middleware, sim_capture
from sim_sdk.http_patch import patch_requests, unpatch_requests
from sim_sdk.db_adapter import SimDB, SimWriteBlocked

__version__ = "0.1.0"

__all__ = [
    # Context
    "SimContext",
    "SimMode",
    "get_context",
    "set_context",
    "clear_context",
    # Canonicalization
    "canonicalize",
    "fingerprint",
    # Redaction
    "redact",
    "DEFAULT_REDACT_PATHS",
    # Store
    "StubStore",
    # Clock
    "SimClock",
    "sim_clock",
    # Flask
    "sim_middleware",
    "sim_capture",
    # HTTP
    "patch_requests",
    "unpatch_requests",
    # DB
    "SimDB",
    "SimWriteBlocked",
]
