"""
sim_sdk - Pure Python SDK for deterministic capture and replay

This is a framework-agnostic library that provides:
- @sim_trace decorator for function tracing
- sim_capture() context manager for operation capture
- sim_db() context manager for database query capture

Zero dependencies on web frameworks, HTTP libraries, or database drivers.
"""

from .context import SimContext
from .trace import sim_trace
from .capture import sim_capture
from .db import sim_db
from .canonical import (
    canonicalize_json,
    fingerprint,
    fingerprint_short,
    normalize_sql,
    fingerprint_sql,
)
from .config import SimConfig, load_config
from .redaction import redact, pseudonymize, create_redactor, create_pseudonymizer
from .sink import RecordSink
from .sink.local import LocalSink
from .sink.s3 import S3Sink
from .fixture import Fixture, CaptureRecord, TraceRecord, FixtureWriter

__version__ = "0.1.0"

__all__ = [
    # Context
    "SimContext",
    # Primitives
    "sim_trace",
    "sim_capture",
    "sim_db",
    # Canonicalization & Fingerprinting
    "canonicalize_json",
    "fingerprint",
    "fingerprint_short",
    "normalize_sql",
    "fingerprint_sql",
    # Configuration
    "SimConfig",
    "load_config",
    # Redaction & Pseudonymization
    "redact",
    "pseudonymize",
    "create_redactor",
    "create_pseudonymizer",
    # Sinks
    "RecordSink",
    "LocalSink",
    "S3Sink",
    # Fixtures
    "Fixture",
    "CaptureRecord",
    "TraceRecord",
    "FixtureWriter",
]
