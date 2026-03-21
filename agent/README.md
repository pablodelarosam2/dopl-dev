# record-agent

Go daemon that receives fixture events from `sim_sdk`, aggregates them into session bundles, buffers them to disk, and optionally uploads to S3. Deployed as a Kubernetes DaemonSet — one agent per node, co-located with application pods.

## Purpose

The record-agent is the persistence layer for recorded fixtures. The Python SDK captures dependency interactions inside application processes and streams them to the agent over HTTP. The agent validates, groups events by session, writes completed sessions atomically to a local spool directory, and (when configured) uploads them to S3 for long-term storage and replay.

The agent does **not** participate in replay. Replay is handled entirely by the SDK and the `sim-replay` CLI.

## Package Structure

```
agent/
├── cmd/record-agent/         # Binary entrypoint (main.go)
├── internal/
│   ├── config/               # Environment-based configuration
│   ├── ingest/               # HTTP handler, wire protocol, validation, queue
│   ├── session/              # Per-session event aggregation and lifecycle
│   ├── spool/                # Atomic disk writes, capacity management, LRU eviction
│   ├── uploader/             # S3 upload worker pool with backoff
│   ├── health/               # Liveness and readiness probes
│   ├── metrics/              # Prometheus instrumentation
│   ├── logging/              # Structured slog setup
│   └── security/             # Auth helpers
├── pkg/                      # Public packages (reserved for external consumers)
└── test/integration/         # Docker-compose E2E tests
```

## Ingestion Pipeline

Events flow through a linear pipeline from HTTP receipt to disk:

```
HTTP POST /v1/events (BatchRequest: []EventEnvelope)
    │
    ▼
Decoder — JSON decode with body size limit (AGENT_MAX_BATCH_BYTES)
    │
    ▼
Validator — Schema version, event type, ID charset/length, timestamp drift, payload size
    │
    ▼
Queue — Bounded channel with non-blocking enqueue (drops on overflow)
    │
    ▼
Session Manager — Groups events by SessionID
    │  ├── Input event    → sets session input
    │  ├── Stub event     → appends to session stubs
    │  ├── Output event   → sets golden output, triggers commit
    │  └── Metadata event → appends to session metadata
    │
    ▼
Spool — Atomic write to disk
    ├── Write to {spoolDir}/{fixtureID}.tmp/fixture.json
    ├── Rename .tmp → final directory (atomic commit)
    └── LRU eviction if spool exceeds AGENT_MAX_SPOOL_BYTES
    │
    ▼
Uploader (optional) — Background scan + S3 PutObject
    ├── Worker pool (AGENT_UPLOAD_WORKERS)
    ├── Exponential backoff on failure (500ms × 2^attempt, cap 30s)
    └── Deletes local fixture after successful upload
```

## Session Lifecycle

A session represents a single recorded request/response cycle:

```
Session created (first event with a new SessionID)
    │
    ├── Accumulates Input, Stub, Metadata events
    │
    ├── Completed by one of:
    │   ├── Output event received (normal completion)
    │   ├── Session byte limit exceeded (AGENT_MAX_SESSION_BYTES)
    │   └── Session age exceeded (AGENT_MAX_SESSION_AGE_MS)
    │
    ▼
FixtureBundle built → spool.Commit()
```

Sessions are tracked in-memory by the Session Manager (single goroutine, mutex-protected map). On shutdown, all active sessions are flushed.

## Wire Format

The SDK and agent share a JSON wire format over HTTP:

**Request** (`POST /v1/events`):
```json
{
  "SchemaVersion": 1,
  "Events": [
    {
      "SchemaVersion": 1,
      "FixtureID": "...",
      "SessionID": "...",
      "EventType": "Stub",
      "TimestampMs": 1234567890,
      "Payload": { ... },
      "Service": "my-service",
      "Trace": "trace-id"
    }
  ]
}
```

**Response**:
```json
{
  "Accepted": 5,
  "Dropped": 0,
  "DroppedByReason": {},
  "Invalid": 0
}
```

Event types: `Input`, `Stub`, `Output`, `Metadata`.

## Fixture Output

Each committed session produces a directory on disk:

```
{spoolDir}/{fixtureID}/
└── fixture.json
```

The fixture JSON contains:
```json
{
  "schema_version": 1,
  "fixture_id": "...",
  "session_id": "...",
  "created_at_ms": 1234567890,
  "stubs": [ ... ],
  "golden_output": {
    "input": { ... },
    "output": { ... }
  }
}
```

## Agent Lifecycle

### Startup

1. Load configuration from environment variables.
2. Initialize spool directory, recover incomplete `.tmp` writes, scan committed fixtures for byte accounting.
3. Build the ingest pipeline: Validator → Queue → Ingestor.
4. Start Session Manager goroutine (consumes from queue).
5. Start Uploader goroutine if `AGENT_S3_BUCKET` is set.
6. Start HTTP server on `AGENT_LISTEN`.

### Steady State

- **HTTP handler**: Decodes POST body → `Ingestor.IngestBatch()` → returns accepted/dropped counts.
- **Session worker**: Reads from queue channel, aggregates by session, commits completed sessions to spool.
- **Uploader**: Periodic scan of spool directory, dispatches fixtures to worker pool for S3 upload.

### Shutdown (SIGTERM/SIGINT)

1. HTTP server graceful shutdown (10s drain).
2. Context cancellation propagates to all goroutines.
3. Uploader drains in-flight uploads (15s timeout).
4. Session Manager flushes active sessions (15s timeout).

## Back-Pressure and Resilience

| Boundary | Mechanism |
|----------|-----------|
| HTTP body | `MaxBytesHandler` rejects oversized requests |
| Ingest validation | Invalid events are dropped with counters, rate-limited logging |
| Queue | Bounded channel; `TryEnqueue` drops on overflow (non-blocking) |
| Session count | `AGENT_MAX_ACTIVE_SESSIONS` cap |
| Session size | `AGENT_MAX_SESSION_BYTES` triggers early commit |
| Spool capacity | LRU eviction of oldest fixtures when over `AGENT_MAX_SPOOL_BYTES` |
| Upload failure | Exponential backoff, max retries; failed fixtures remain on disk |
| Panics | `panicRecoveryMiddleware` catches and returns 500 |

## HTTP Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/events` | POST | Receive batched fixture events from sim_sdk |
| `/live` | GET | Liveness probe (always 200) |
| `/ready` | GET | Readiness probe (checks spool, queue, sessions, uploader thresholds) |

## Configuration

All configuration is via environment variables.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_LISTEN` | `127.0.0.1:7777` | HTTP bind address |
| `AGENT_SPOOL_DIR` | `/tmp/record-agent` | Spool root directory |
| `AGENT_MAX_SPOOL_BYTES` | `5368709120` (5 GB) | Max spool disk usage |
| `AGENT_MAX_ACTIVE_SESSIONS` | `1000` | Max concurrent sessions |
| `AGENT_MAX_SESSION_BYTES` | `1048576` (1 MB) | Per-session byte limit |
| `AGENT_MAX_SESSION_AGE_MS` | `60000` | Session TTL before forced commit (ms) |
| `AGENT_MAX_EVENT_BYTES` | `262144` (256 KB) | Max single event size |
| `AGENT_MAX_BATCH_BYTES` | `10485760` (10 MB) | Max POST body size |
| `AGENT_INGEST_QUEUE_SIZE` | `10000` | Ingest queue capacity |
| `AGENT_FLUSH_INTERVAL_MS` | `2000` | Internal flush interval (ms) |
| `AGENT_LOG_LEVEL` | `info` | Log level (`debug`, `info`, `warn`, `error`) |

### S3 Uploader (optional)

The uploader activates only when `AGENT_S3_BUCKET` is set. It scans the spool for committed fixtures, uploads them to S3 via `PutObject`, and removes the local copy after a successful upload.

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_S3_BUCKET` | _(empty — uploader disabled)_ | Target S3 bucket name |
| `AGENT_S3_REGION` | `us-east-1` | AWS region for the S3 client |
| `AGENT_S3_PREFIX` | _(empty)_ | Key prefix: `{prefix}/{fixtureID}/fixture.json` |
| `AGENT_UPLOAD_WORKERS` | `4` | Concurrent upload goroutines |
| `AGENT_UPLOAD_INTERVAL` | `5s` | How often the spool is scanned for new fixtures |
| `AGENT_UPLOAD_MAX_RETRIES` | `3` | PutObject retry attempts per fixture |

### AWS Credentials

When the S3 uploader is enabled, the agent uses the [AWS SDK v2 default credential chain](https://docs.aws.amazon.com/sdkref/latest/guide/standardized-credentials.html). Credentials are resolved in this order:

1. **Environment variables** — `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and optionally `AWS_SESSION_TOKEN`.
2. **Shared credentials file** — `~/.aws/credentials` (supports named profiles via `AWS_PROFILE`).
3. **EC2 instance metadata / ECS task role / EKS pod identity** — automatic in AWS environments.

For local development:

```bash
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=wJal...
# export AWS_SESSION_TOKEN=...   # only needed for temporary/assumed-role credentials
```

In production (EKS, EC2, ECS), attach an IAM role to the pod or instance. No environment variables are needed — the SDK discovers credentials automatically.

## Deployment

The agent ships as a Helm chart for Kubernetes DaemonSet deployment:

```
deploy/helm/record-agent/
├── Chart.yaml
├── values.yaml
└── templates/
```

The DaemonSet runs one agent pod per node. Application pods on the same node send events to the agent over localhost.

## Development

```bash
go build ./cmd/record-agent
go test ./...
```

Integration tests use Docker Compose:

```bash
cd test/integration
docker-compose up --build
```
