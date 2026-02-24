# record-agent

The `record-agent` is a Kubernetes DaemonSet that runs on every node in the cluster. It receives fixture
data emitted by the `sim_sdk` (via a local Unix socket or gRPC endpoint), spools records to disk, and
uploads completed session bundles to S3 for later replay by the simulation runner.

## Directory layout

```
agent/
├── cmd/record-agent/   # CLI entrypoint
├── internal/           # Private implementation packages
│   ├── config/         # Configuration loading (env + file)
│   ├── ingest/         # Wire protocol / receive side
│   ├── session/        # Per-request session lifecycle
│   ├── spool/          # Disk buffering & rotation
│   ├── uploader/       # S3 upload (Phase 2+)
│   ├── metrics/        # Prometheus metrics
│   ├── health/         # HTTP health & readiness probes
│   ├── logging/        # Structured logger setup
│   └── security/       # AuthN/AuthZ helpers
├── pkg/                # Packages exported for external consumers
└── test/integration/   # End-to-end tests (docker-compose based)
```

## Development

```bash
go build ./...
go test ./...
```
