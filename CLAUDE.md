# CLAUDE.md — dopl-dev Project Guide

## 0. Hard Rule: Zero Framework Dependencies

The `sim_sdk/` package must contain **zero imports** from any web framework, HTTP library, or database driver.

This is not a guideline — it is a **hard constraint enforced in CI**. The SDK is a pure Python library that works on plain functions. It does not know about Flask, Django, FastAPI, requests, httpx, psycopg2, SQLAlchemy, or any other framework or driver.

### What the SDK knows about

- Python functions (via decorators)
- Python context managers (via `with` blocks)
- Python dicts, lists, strings, numbers (the data it captures)
- `contextvars` (for request-scoped state)
- Standard library: `json`, `hashlib`, `contextvars`, `logging`, `time`, `uuid`
- `boto3` (S3 only, isolated in `sim_sdk/sink/s3.py`)

### What the SDK does NOT know about

- How HTTP requests arrive (Flask, FastAPI, raw socket — not the SDK's problem)
- How HTTP calls are made (requests, httpx, aiohttp — not the SDK's problem)
- How DB queries are executed (psycopg2, SQLAlchemy, asyncpg — the consumer passes a DB object, the SDK wraps it generically)
- Request/response objects, middleware hooks, route decorators

### CI enforcement

```python
# In CI pipeline: fail if sim_sdk/ imports any banned module
banned = ['flask', 'django', 'fastapi', 'starlette', 'requests',
          'httpx', 'aiohttp', 'psycopg2', 'sqlalchemy', 'asyncpg']
# Scan all .py files in sim_sdk/ for 'import <banned>' or 'from <banned>'
```

### The boundary

The SDK provides primitives. The consumer (a Flask app, a Kotlin service, a plain script) uses those primitives in whatever framework it likes. Examples in `examples/` show how, but `examples/` is **not** part of the SDK package.

---

## 1. Project Overview

**dopl-dev** is a simulation platform for validating code changes by comparing baseline (main) vs candidate (PR) outputs using recorded fixtures and dependency stubs.

**Core value**: Catch "200 OK but wrong" bugs — logic regressions that pass all existing tests but produce incorrect business results.

**How it works**: Record production-like traffic as fixtures, replay against PR branches, diff the outputs, surface anomalies.

---

## 2. Repository Structure

```
dopl-dev/
├── sim_sdk/                      # Python package (pip-installable)
│   ├── sim_sdk/                  # Source code
│   │   ├── __init__.py           # Public API exports
│   │   ├── canonical.py          # JSON canonicalization & fingerprinting
│   │   ├── capture.py            # sim_capture() context manager
│   │   ├── config.py             # Configuration loader (sim.yaml)
│   │   ├── context.py            # SimContext + contextvars management
│   │   ├── db.py                 # sim_db() context manager
│   │   ├── redaction.py          # PII redaction & pseudonymization
│   │   ├── trace.py              # @sim_trace decorator
│   │   ├── fixture/              # Fixture schemas and writers
│   │   │   ├── schema.py         # Fixture, TraceRecord, CaptureRecord
│   │   │   └── writer.py         # FixtureWriter
│   │   └── sink/                 # Recording sinks
│   │       ├── __init__.py       # RecordSink ABC
│   │       ├── local.py          # LocalSink (filesystem)
│   │       └── s3.py             # S3Sink (cloud, lazy boto3)
│   ├── pyproject.toml            # Package metadata
│   └── requirements*.in          # Dependency specs
├── tests/                        # All tests (outside the package)
│   ├── test_canonical.py
│   ├── test_capture.py
│   ├── test_context.py
│   ├── test_db.py
│   ├── test_integration.py
│   ├── test_redaction.py
│   ├── test_sink_local.py
│   ├── test_sink_s3.py
│   └── test_trace.py
├── examples/                     # Consumer integration examples
│   ├── plain_python/             # No framework needed
│   └── flask_app/                # Flask uses the SDK (not vice versa)
├── README.md
├── VALIDATION_REPORT.md
└── CLAUDE.md                     # This file
```

---

## 3. Public API

All public symbols are exported from `sim_sdk/__init__.py`:

| Symbol | Module | Purpose |
|--------|--------|---------|
| `SimContext` | `context.py` | Request-scoped simulation state (contextvars) |
| `init_sim()` | `context.py` | Initialize context at app startup |
| `@sim_trace` | `trace.py` | Decorator to trace function calls |
| `sim_capture()` | `capture.py` | Context manager for capturing operations |
| `sim_db()` | `db.py` | Context manager for wrapping DB connections |
| `canonicalize_json()` | `canonical.py` | Deterministic JSON serialization |
| `fingerprint()` | `canonical.py` | SHA-256 content hash |
| `fingerprint_short()` | `canonical.py` | Truncated fingerprint |
| `normalize_sql()` | `canonical.py` | SQL query normalization |
| `fingerprint_sql()` | `canonical.py` | SQL-specific fingerprint |
| `SimConfig` | `config.py` | Configuration dataclass |
| `load_config()` | `config.py` | Load from sim.yaml / env |
| `redact()` | `redaction.py` | Replace PII with placeholders |
| `pseudonymize()` | `redaction.py` | Deterministic PII hashing |
| `create_redactor()` | `redaction.py` | Reusable redactor factory |
| `create_pseudonymizer()` | `redaction.py` | Reusable pseudonymizer factory |
| `RecordSink` | `sink/__init__.py` | ABC for recording backends |
| `LocalSink` | `sink/local.py` | Filesystem sink |
| `S3Sink` | `sink/s3.py` | S3 sink (lazy boto3) |
| `Fixture` | `fixture/schema.py` | Fixture data container |
| `CaptureRecord` | `fixture/schema.py` | Captured operation block |
| `TraceRecord` | `fixture/schema.py` | Single traced function call |
| `FixtureWriter` | `fixture/writer.py` | Writes fixtures to sinks |

---

## 4. Coding Conventions

### Typing

All public functions and methods must have type hints:

```python
def fingerprint(obj: Any) -> str: ...
def redact(data: Dict[str, Any], fields: List[str]) -> Dict[str, Any]: ...
```

### Data models

Use `@dataclass` for structured data:

```python
@dataclass
class SimContext:
    mode: SimMode = SimMode.OFF
    run_id: str = ""
```

### Context managers over decorators for resource scoping

```python
with sim_capture("process_order", order_id=order_id):
    ...
with sim_db(db_conn, name="orders_db") as db:
    ...
```

### Lazy loading for optional dependencies

```python
try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False
```

### Context management

Use `contextvars.ContextVar` for request-scoped state. This is both thread-safe and async-safe. Never use `threading.local()` or global mutable state.

```python
from contextvars import ContextVar
_context_var: ContextVar[Optional[SimContext]] = ContextVar("sim_context", default=None)
```

### Environment variables

| Variable | Purpose |
|----------|---------|
| `SIM_MODE` | `off`, `record`, `replay` |
| `SIM_RUN_ID` | Unique run identifier |
| `SIM_STUB_DIR` | Path for stub/fixture files |
| `SIM_CONFIG` | Path to sim.yaml |

---

## 5. Testing

### Running tests

```bash
cd sim_sdk && pip install -e ".[dev]"
pytest ../tests/ -v
```

### Test naming

```python
def test_<module>_<scenario>():
    """Descriptive sentence about what's being tested."""
```

### Test patterns

- Use `pytest.fixture` for temp directories, mock objects
- Use `unittest.mock` for mocking (no external mock libraries)
- Each test file maps to one SDK module
- `test_integration.py` covers end-to-end flows

### No framework imports in tests

Tests must also avoid importing banned frameworks. Use plain mock objects:

```python
class MockDatabase:
    def cursor(self):
        return MockCursor()
```

---

## 6. Working on This Project

### Before making changes

1. Read the relevant source file(s) first
2. Check `__init__.py` for what's currently exported
3. Run existing tests to make sure they pass

### Adding new SDK modules

1. Create the module in `sim_sdk/sim_sdk/`
2. Export public symbols from `__init__.py`
3. Add tests in `tests/test_<module>.py`
4. Verify zero banned imports (Rule 0)

### Adding integration examples

Put them in `examples/<name>/` with their own `requirements.txt`. Framework dependencies go there, never in `sim_sdk/`.

---

## 7. Clean Code Patterns

This codebase follows Robert C. Martin's *Clean Code* principles. All contributors must adhere to these patterns.

### Functions

- **Small functions**: Each function does one thing. If a function has more than ~20 lines, consider splitting it.
- **One level of abstraction per function**: Don't mix high-level orchestration with low-level detail in the same function. Extract helpers.
- **Intent-revealing names**: `start_new_request()` not `new_request_id()`. The name should describe the *why*, not the *what*.
- **No side effects hidden in names**: If a function resets state, the name must say so.

### DRY (Don't Repeat Yourself)

- **Extract shared logic into helpers**: If the same pattern appears in sync and async wrappers, extract it.
  ```python
  # Good: shared helper
  args_data, input_fp, ordinal = _prepare_call(f, qualname, args, kwargs, ctx)

  # Bad: duplicated fingerprint logic in both sync_wrapper and async_wrapper
  ```
- **Pass data explicitly**: Prefer passing values as parameters over reaching into shared state.
  ```python
  # Good: caller scopes the stubs and passes them
  _emit_record(..., inner_stubs=inner_stubs)

  # Bad: function reaches into ctx.collected_stubs and clears it
  ```

### Memory safety

- **No unbounded growth**: Dicts and lists that grow per-request must have a `reset()` path.
- **No monkey-patching**: Use proper dataclass fields instead of `setattr()` on objects at runtime.
  ```python
  # Good: declared field
  @dataclass
  class SimContext:
      trace_depth: int = 0

  # Bad: dynamic attribute
  ctx._trace_depth = getattr(ctx, "_trace_depth", 0) + 1
  ```
- **Scope resources with try/finally**: Especially `collected_stubs` — use snapshot/slice to scope per trace level.
  ```python
  stubs_snapshot = len(ctx.collected_stubs)
  try:
      output = f(*args, **kwargs)
  finally:
      inner_stubs = list(ctx.collected_stubs[stubs_snapshot:])
      del ctx.collected_stubs[stubs_snapshot:]
  ```

### Error handling

- **Custom exceptions with diagnostics**: Include enough context to debug.
  ```python
  class SimStubMissError(Exception):
      def __init__(self, qualname, input_fingerprint, ordinal, stub_dir=None):
          # Include qualname, fingerprint, ordinal, and expected file path
  ```
- **Never swallow exceptions silently**: Log or re-raise.

### Testing

- **Each test class maps to one acceptance criterion** (e.g., `TestRecordMode`, `TestReplayMode`).
- **Tests are independent**: Each test creates its own context via fixtures. No shared mutable state between tests.
- **Use `autouse=True` clean_context fixture** to reset the ContextVar between tests.
- **Test the boundary**: Zero-dependency tests scan source for banned imports.

---

## 8. Architecture Principles

1. **The SDK is a library of primitives** — not a framework, not middleware
2. **Consumers integrate the SDK** — the SDK never integrates itself into consumers
3. **The runner is a separate service** — not part of the SDK package
4. **Determinism is non-negotiable** — same input must produce same fingerprint across runs
5. **Privacy by default** — redaction and pseudonymization are first-class operations
6. **Graceful degradation** — optional dependencies fail cleanly when absent
