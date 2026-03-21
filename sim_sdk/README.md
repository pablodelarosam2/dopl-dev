# sim_sdk

Pure-Python SDK for recording and replaying application dependencies. Zero framework imports — works with Flask, Django, FastAPI, or plain Python.

## Purpose

sim_sdk instruments your application code to capture every external interaction (database queries, HTTP calls, arbitrary side-effects) during recording, and substitutes them with deterministic stubs during replay. The SDK uses `contextvars.ContextVar` for all state management, making it safe for threaded and async environments without global mutable state.

## Package Structure

```
sim_sdk/
├── sim_sdk/                  # Core library
│   ├── __init__.py           # Public API exports
│   ├── context.py            # SimContext, SimMode, ContextVar state
│   ├── trace.py              # @sim_trace decorator
│   ├── capture.py            # sim_capture context manager
│   ├── db.py                 # sim_db, DBProxy
│   ├── http.py               # sim_http, HTTPProxy, FakeResponse
│   ├── replay_context.py     # ReplayContext, per-request replay state
│   ├── stub_store.py         # StubStore — fixture index and lookup
│   ├── canonical.py          # JSON canonicalization, fingerprinting, SQL normalization
│   ├── config.py             # SimConfig, sim.yaml loader
│   ├── redaction.py          # PII redaction and pseudonymization
│   ├── errors.py             # SimStubMissError
│   ├── fixture/
│   │   └── schema.py         # FixtureEvent dataclass
│   └── sink/
│       ├── record_sink.py    # RecordSink (abstract base)
│       ├── agent_sink.py     # AgentSink — sends events to record-agent
│       ├── agent_client.py   # AgentHttpClient (stdlib urllib only)
│       ├── envelope.py       # EventEnvelope, BatchRequest wire format
│       ├── in_memory_buffer.py
│       ├── sender_worker.py  # Background flush thread
│       └── sender_metrics.py
├── sim_runner/
│   └── replay_cli.py         # sim-replay CLI entrypoint
└── tests/
```

## Modes of Operation

The SDK operates in one of three modes, controlled by `SIM_MODE`:

| Mode | Behavior |
|------|----------|
| `off` | All primitives pass through transparently. No capture, no replay. |
| `record` | Real I/O executes. Inputs and outputs are serialized, fingerprinted, and emitted as fixture events. |
| `replay` | Real I/O is skipped. Stubs are looked up by fingerprint + ordinal from a loaded fixture file. |

## Core Primitives

### `@sim_trace` — Function Boundary

Marks a function as the root (or nested) boundary of a recorded interaction. In record mode, it captures the function's arguments and return value. In replay mode, it returns the recorded output without executing the function body.

**Fingerprint**: `qualname` + `canonicalize_json(args)` → SHA-256.

### `sim_db` — Database Proxy

Context manager that wraps a database connection object. In record mode, queries execute normally and results are captured. In replay mode, recorded rows are returned. Write statements (`INSERT`, `UPDATE`, `DELETE`) raise `SimWriteBlockedError` during replay to prevent side-effects.

**Fingerprint**: `normalize_sql(query)` + `fingerprint(params)`.

**Duck-typed**: Works with any object exposing `.query(sql, params)` or `.execute(sql, params)`. No database driver imports.

### `sim_http` — HTTP Client Proxy

Context manager that wraps an HTTP client object. In record mode, requests execute normally and responses are captured. In replay mode, a `FakeResponse` is returned with the recorded status code, body, and headers.

**Fingerprint**: `normalize_url(url)` + `fingerprint(body)` + stable header subset.

**Duck-typed**: Works with any object exposing `.get()`, `.post()`, `.request()`, etc. Response extraction uses `.status_code`, `.text`, `.headers` via duck typing.

### `sim_capture` — Arbitrary Block Capture

Context manager for capturing any side-effect or computation. Yields a `CaptureHandle` that exposes `.set_result(value)` for recording and `.result` / `.replaying` for replay.

## Execution Flow: Recording

```
1. Application starts with SIM_MODE=record
2. init_sim() creates a SimContext with mode=RECORD, stored in ContextVar
3. Request arrives and hits @sim_trace
4. @sim_trace increments trace_depth, fingerprints input args
5. Inside the function body:
   a. sim_db wraps the DB connection → DBProxy
   b. DBProxy.query() runs the real query, serializes rows
   c. A FixtureEvent is emitted to ctx.sink (or written to stub_dir)
   d. sim_http wraps the HTTP client → HTTPProxy
   e. HTTPProxy.get() runs the real request, captures the response
   f. Another FixtureEvent is emitted
6. @sim_trace collects all inner stubs, serializes the return value
7. A final FixtureEvent with type=Output is emitted
8. The sink (AgentSink) batches events and flushes to the record-agent
```

## Execution Flow: Replay

```
1. Application starts with SIM_MODE=replay
2. A request arrives with x-sim-fixture-name header
3. Middleware creates a ReplayContext:
   a. Loads the fixture JSON from fixture_dir
   b. StubStore.from_fixture() indexes all stubs by type and fingerprint
   c. ReplayContext is set in its own ContextVar
4. @sim_trace matches fingerprint + ordinal → returns recorded output
5. If the function body runs (nested trace or inner calls):
   a. sim_db looks up stub_store.get_db_stub(fingerprint, ordinal)
   b. Returns recorded rows; real DB is never touched
   c. sim_http looks up stub_store.get_http_stub(name, ordinal)
   d. Returns FakeResponse; real HTTP client is never called
6. Output is produced from recorded data only
7. Output can be compared against golden_output for regression detection
```

## State Management

Two `ContextVar` instances manage all request-scoped state:

| ContextVar | Class | Contents |
|------------|-------|----------|
| `_context_var` | `SimContext` | Mode, run_id, stub_dir, sink, ordinal counters, collected_stubs, trace_depth |
| `_sim_replay_context` | `ReplayContext` | Fixture ID, StubStore, per-type ordinal counters (DB, HTTP, trace) |

No global mutable state. No `threading.local()`. Safe for threaded WSGI servers and async frameworks.

## Sink Architecture

When recording, fixture events flow through a sink pipeline:

```
FixtureEvent
    → RecordSink.emit()
        → InMemoryBuffer (bounded, drops on overflow)
            → SenderWorker (daemon thread, periodic flush)
                → AgentHttpClient.post_batch()
                    → HTTP POST /v1/events (PascalCase JSON)
                        → record-agent
```

`AgentHttpClient` uses only `urllib.request` — no third-party HTTP libraries.

## Fingerprinting and Determinism

All stub lookups depend on deterministic fingerprints:

1. **`canonicalize_json(obj)`** — Sorts dict keys recursively, produces stable JSON bytes.
2. **`fingerprint(obj)`** — `SHA-256(canonicalize_json(obj))`, truncated hex.
3. **`normalize_sql(sql)`** — Strips whitespace, lowercases keywords (optional `sqlparse`).
4. **Ordinals** — Per-fingerprint counter that increments on each call within a request, disambiguating repeated identical calls.

## Replay CLI

`sim-replay` is a standalone CLI for driving replay against a running service:

```bash
sim-replay \
  --fixture-dir ./fixtures \
  --host localhost \
  --port 8080 \
  --path /api/endpoint \
  --output-dir ./results
```

It loads each fixture file, sends `golden_output.input` as the request body with `x-sim-fixture-name` and `x-sim-run-id` headers, and writes the captured response for comparison.

## Installation

```bash
pip install -e ".[dev]"
```

## Testing

```bash
pytest tests/ -v
```

Tests use `pytest` fixtures and `unittest.mock` only. No framework imports in test files.
