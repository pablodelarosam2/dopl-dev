# dopl-dev

A simulation platform that catches **"200 OK but wrong"** bugs by recording production-like traffic and replaying it deterministically against code changes.

## What It Does

dopl-dev records the external dependencies of your application (database queries, HTTP calls, arbitrary side-effects) during normal operation, then replays those recordings during PR validation. By comparing the output of your baseline code against a candidate branch using identical inputs, it detects semantic regressions that unit tests and status-code checks miss.

## Architecture

The system is composed of two independent components that communicate over a local HTTP channel:

```
┌─────────────────────────────────────────────────────────┐
│                   Application Process                   │
│                                                         │
│   @sim_trace ──► sim_db / sim_http / sim_capture        │
│        │                    │                           │
│        ▼                    ▼                           │
│    SimContext (ContextVar)                               │
│        │                                                │
│        ▼                                                │
│    AgentSink ──► InMemoryBuffer ──► SenderWorker        │
│                                        │                │
└────────────────────────────────────────│────────────────┘
                                         │
                          HTTP POST /v1/events
                          (localhost:9700)
                                         │
┌────────────────────────────────────────▼────────────────┐
│                   record-agent (Go)                     │
│                                                         │
│    Ingestor ──► Queue ──► Session Manager ──► Spool     │
│                                                  │      │
│                                           ┌──────┘      │
│                                           ▼             │
│                                     Uploader ──► S3     │
└─────────────────────────────────────────────────────────┘
```

| Component | Language | Role |
|-----------|----------|------|
| **sim_sdk** | Python | Pure-Python library that instruments application code. Captures and replays DB queries, HTTP calls, and arbitrary blocks. Framework-agnostic (no Flask/Django/FastAPI imports). |
| **record-agent** | Go | Kubernetes DaemonSet that receives events from the SDK, aggregates them into fixture bundles on disk, and optionally uploads to S3. |

## How Recording Works (Ingestion Flow)

When `SIM_MODE=record`, the SDK intercepts every dependency call and emits a fixture event:

```
1. Application calls a @sim_trace-decorated function
2. Inside, sim_db / sim_http / sim_capture wrap real dependencies
3. Real I/O executes normally (DB queries run, HTTP calls go out)
4. Each call's input + output is serialized and fingerprinted (SHA-256)
5. Events are emitted to a RecordSink (AgentSink or file-based)
6. AgentSink batches events in memory, flushes via HTTP POST to the record-agent
7. The agent validates, groups events by session, and writes fixture bundles to disk
8. (Optional) The uploader scans the spool and pushes fixtures to S3
```

**Fixture format**: Each completed session produces a single JSON file containing:
- `stubs` — recorded DB results, HTTP responses, and capture blocks
- `golden_output` — the root trace's input and output (the expected behavior)

## How Replay Works

When `SIM_MODE=replay`, the SDK replaces real I/O with recorded stubs:

```
1. sim-replay CLI (or test harness) loads a fixture file
2. It sends the fixture's golden_output.input as a POST to the running service
3. Request headers include x-sim-fixture-name and x-sim-run-id
4. Application middleware creates a ReplayContext and loads the fixture's StubStore
5. @sim_trace matches the input fingerprint + call ordinal to a recorded stub
6. sim_db returns recorded rows instead of querying a real database
7. sim_http returns a FakeResponse instead of making a real HTTP call
8. The application produces output using only recorded data
9. The output is compared against golden_output to detect regressions
```

**No real dependencies are needed during replay** — no database, no external APIs, no network.

## Determinism Model

Stable replay depends on two mechanisms:

- **Fingerprinting**: Every captured call is hashed using `canonicalize_json` (sorted keys, stable serialization) followed by SHA-256. This produces a deterministic lookup key regardless of dict ordering or whitespace.
- **Ordinal counters**: When the same fingerprint appears multiple times in a request (e.g., the same SQL query called in a loop), an ordinal counter disambiguates each occurrence. Ordinals are tracked per-fingerprint, per-request.

## Project Structure

```
dopl-dev/
├── sim_sdk/                  # Python SDK (see sim_sdk/README.md)
│   ├── sim_sdk/              #   Core library package
│   ├── sim_runner/           #   Replay CLI (sim-replay)
│   ├── tests/                #   Unit tests
│   └── examples/             #   Usage examples
│
├── agent/                    # Go record-agent (see agent/README.md)
│   ├── cmd/record-agent/     #   Binary entrypoint
│   ├── internal/             #   Private packages
│   └── test/integration/     #   E2E tests
│
├── examples/                 # Full application demos
│   ├── flask_app/            #   Flask app with record/replay
│   └── plain_python/         #   Framework-free demo
│
├── deploy/                   # Deployment manifests
│   ├── helm/record-agent/    #   Helm chart (DaemonSet)
│   └── kustomize/            #   Kustomize overlays
│
└── .github/workflows/        # CI pipelines (SDK, agent, E2E)
```

## Environment Variables

### SDK (Python)

| Variable | Values | Description |
|----------|--------|-------------|
| `SIM_MODE` | `off`, `record`, `replay` | SDK operating mode |
| `SIM_RUN_ID` | UUID string | Unique identifier for the simulation run |
| `SIM_STUB_DIR` | Path | Directory for reading/writing fixture files |
| `SIM_CONFIG` | Path | Path to `sim.yaml` configuration file |

### record-agent (Go)

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

### S3 Uploader (optional — agent only)

The uploader activates when `AGENT_S3_BUCKET` is set. Fixtures are uploaded from the spool to S3 and deleted locally after a successful upload.

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_S3_BUCKET` | _(empty — uploader disabled)_ | Target S3 bucket name |
| `AGENT_S3_REGION` | `us-east-1` | AWS region for the S3 client |
| `AGENT_S3_PREFIX` | _(empty)_ | Key prefix: `{prefix}/{fixtureID}/fixture.json` |
| `AGENT_UPLOAD_WORKERS` | `4` | Concurrent upload goroutines |
| `AGENT_UPLOAD_INTERVAL` | `5s` | How often the spool is scanned for new fixtures |
| `AGENT_UPLOAD_MAX_RETRIES` | `3` | PutObject retry attempts per fixture |

### AWS Credentials (required when S3 uploader is enabled)

The agent uses the [AWS SDK default credential chain](https://docs.aws.amazon.com/sdkref/latest/guide/standardized-credentials.html). For local development, set these environment variables:

| Variable | Description |
|----------|-------------|
| `AWS_ACCESS_KEY_ID` | IAM access key |
| `AWS_SECRET_ACCESS_KEY` | IAM secret key |
| `AWS_SESSION_TOKEN` | _(optional)_ Temporary session token (for STS/assumed roles) |
| `AWS_REGION` | _(optional)_ Overrides the agent's `AGENT_S3_REGION` at the SDK level |

Alternatively, configure credentials via `~/.aws/credentials` and `~/.aws/config` profiles, or use IAM instance/pod roles in production (no env vars needed).

## Quick Start

```bash
# Install the SDK
cd sim_sdk && pip install -e ".[dev]"

# Record against real dependencies
SIM_MODE=record SIM_STUB_DIR=./fixtures python your_app.py

# Replay without any dependencies
SIM_MODE=replay SIM_STUB_DIR=./fixtures python your_app.py

# Or use the replay CLI
sim-replay --fixture-dir ./fixtures --host localhost --port 8080
```

## Local Testing Walkthrough

This section walks through building and running every component locally, then exercising the full record/replay cycle using the Flask example app.

### Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Python | >= 3.9 | `python3 --version` |
| Go | >= 1.22 | `go version` |
| curl | any | `curl --version` |

**Install Go** (if not already installed):

```bash
# macOS (Homebrew)
brew install go

# Linux (official tarball — adjust version as needed)
curl -LO https://go.dev/dl/go1.22.5.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf go1.22.5.linux-amd64.tar.gz
export PATH=$PATH:/usr/local/go/bin
```

Verify: `go version` should print `go1.22` or later.

### Step 1 — Build and Start the record-agent

The agent receives fixture events from the SDK over HTTP. Build and run it first so it's ready when the Flask app starts recording.

```bash
# Terminal 1: Build and run the agent (local spool only, no S3)
cd agent
go build -o record-agent ./cmd/record-agent

AGENT_LISTEN=127.0.0.1:9700 \
AGENT_SPOOL_DIR=/tmp/record-agent-local \
AGENT_LOG_LEVEL=debug \
  ./record-agent
```

You should see the agent start and listen on `127.0.0.1:9700`. Verify with:

```bash
curl -s http://127.0.0.1:9700/live
# Expected: {"status":"ok"}
```

**With S3 upload enabled** (optional — uploads fixtures from spool to S3):

```bash
AGENT_LISTEN=127.0.0.1:9700 \
AGENT_SPOOL_DIR=/tmp/record-agent-local \
AGENT_LOG_LEVEL=debug \
AGENT_S3_BUCKET=my-fixtures-bucket \
AGENT_S3_REGION=us-east-1 \
AGENT_S3_PREFIX=dev \
AWS_ACCESS_KEY_ID=AKIA... \
AWS_SECRET_ACCESS_KEY=wJal... \
  ./record-agent
```

> **AWS credentials**: The agent uses the standard AWS SDK credential chain. For local dev, set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` as shown above. In production (EKS, EC2), use IAM roles instead — no env vars needed. See the [Environment Variables](#aws-credentials-required-when-s3-uploader-is-enabled) section for all options.

> **Note**: The Flask example uses a `LocalFileSink` that writes fixtures directly to disk, so the agent is optional for the basic demo. To test the full SDK-to-agent pipeline, configure `AgentSink` instead (see `sim_sdk/README.md`).

### Step 2 — Set Up the Python Environment

Create an isolated virtual environment and install the SDK and example dependencies.

```bash
# Terminal 2: From the repo root
python3 -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

# Install the SDK in editable mode
pip install -e "./sim_sdk[dev]"

# Install the Flask example dependencies
pip install -r examples/flask_app/requirements.txt
```

### Step 3 — Record Mode (Capture Real Interactions)

Start the Flask app in record mode. Every call to `/quote` will execute real logic and write fixture files to `.sim/fixtures/`.

```bash
# Terminal 2: Start the app in record mode
cd examples/flask_app
SIM_MODE=record python3 app.py
```

The app starts on port `5050`. In a new terminal, send a request:

```bash
# Terminal 3: Send a quote request
curl -s -X POST http://localhost:5050/quote \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": [{"sku": "WIDGET-A", "qty": 2}]}' | python3 -m json.tool
```

You should see a full quote response with `subtotal`, `tax`, `shipping`, and `total` fields.

Check that fixtures were written:

```bash
ls -R examples/flask_app/.sim/fixtures/
# Expected: calculate_quote/, __db__/, __capture__/ directories with .json files
```

Stop the Flask app (`Ctrl+C`).

### Step 4 — Replay Mode (Zero Real I/O)

Start the same app in replay mode. The SDK substitutes every database query, HTTP call, and captured block with recorded stubs. No real dependencies are contacted.

```bash
# Terminal 2: Start the app in replay mode
cd examples/flask_app
SIM_MODE=replay SIM_STUB_DIR=.sim/fixtures python3 app.py
```

Send the same request, this time including the `x-sim-fixture-name` header to tell the SDK which fixture to load:

```bash
# Terminal 3: Replay the same request
curl -s -X POST http://localhost:5050/quote \
  -H "Content-Type: application/json" \
  -H "x-sim-fixture-name: calculate_quote" \
  -d '{"user_id": 1, "items": [{"sku": "WIDGET-A", "qty": 2}]}' | python3 -m json.tool
```

The response should match the recorded output. No database was connected, no HTTP calls were made — all data came from the fixture files.

### Step 5 — Replay via CLI

Instead of manually curling, use the `sim-replay` CLI to drive replay across all fixtures in a directory:

```bash
# Terminal 3: Run the replay CLI (app must still be running in replay mode)
sim-replay \
  --fixture-dir examples/flask_app/.sim/fixtures/calculate_quote \
  --port 5050 \
  --path /quote \
  --output-dir ./replay_results \
  --verbose
```

Results are written to `./replay_results/` as JSON files — one per fixture — for comparison against the golden output.

### Step 6 — Health Check

At any point, check the app's simulation state:

```bash
curl -s http://localhost:5050/health | python3 -m json.tool
# Shows: sim_mode, stub_dir, sink type
```

### Summary of Terminals

| Terminal | Command | Purpose |
|----------|---------|---------|
| 1 | `./record-agent` | Agent receiving fixture events (optional for file-based sink) |
| 2 | `SIM_MODE=record python3 app.py` | Flask app in record or replay mode |
| 3 | `curl ...` / `sim-replay ...` | Send requests or run the replay CLI |

## License

MIT
