# PROJECT X — Phase 3: Fixture Service

**Engineer-Level Task Specification**
March 2026 • Duration: 4–5 days • Engineers: 2 • 7 Tasks
Status: Pre-Development | Classification: Internal

---

## Core Principle

> **Fixtures Are a Living Corpus, Not a Static Snapshot**
> Recording is continuous. The fixture store grows as real traffic flows through the service. Retrieval always favours the most recent recordings because dependency outputs drift over time. Staleness is a first-class concern.

---

## 1. Goal

Build the infrastructure to index, store, and retrieve recorded fixtures from S3 so that the replay runner (Phase 4) and CI workflows can fetch the right fixtures on demand, without relying on local disk.

This phase bridges Phase 2 (local replay) and Phase 4 (CI-integrated runner) by providing a durable, queryable fixture store backed by S3 + Postgres.

---

## 2. Architecture Overview

The fixture pipeline has three stages, each owned by a distinct process:

- **Fixture Daemon** (existing, Phase 2): Runs as a sidecar to the Flask service. Watches local disk for new fixtures, uploads to S3 with a structured prefix key. Phase 3 adds: sampling logic + structured S3 key generation.
- **Indexer** (new): A long-running process in the cloud (k8s pod). Consumes S3 object-creation events via SQS, parses fixture metadata, writes index rows to Postgres. Handles deduplication by content hash.
- **Retrieval API + CLI** (new): A lightweight FastAPI service (k8s pod) that queries Postgres and returns fixture manifests. A CLI wrapper (sim-fetch) calls the API and stages fixtures locally for the replay runner.

### Why SQS (not RabbitMQ or polling)

S3 natively pushes object-creation events to SQS with zero code — it is a bucket configuration toggle. The Indexer polls SQS using boto3. No bridge service, no dual-write from the daemon, no watermark state to manage. If the Indexer is down, messages queue up and are processed on recovery. SQS can be swapped for RabbitMQ later by changing only the consumer interface.

---

## 3. S3 Prefix Layout

All fixtures are stored under a structured prefix:

```
s3://{bucket}/fixtures/{service}/{endpoint_key}/{date}/{fixture_id}.json
```

- **service**: The service name from sim.yaml (e.g., `pricing-api`).
- **endpoint_key**: Slugified method + path (e.g., `POST_quote`, `GET_checkout_status`). Lowercase, slashes replaced with underscores.
- **date**: ISO date of recording (e.g., `2026-03-21`). Enables prefix-based TTL and human navigation.
- **fixture_id**: UUID assigned at recording time. The fixture JSON and its stubs are uploaded as a single file (the per-fixture stub model from Phase 1/2 is preserved).

This layout supports S3 ListObjects as a fallback if the DB index is unavailable, enables lifecycle policies for automated TTL, and is human-navigable via the AWS console or CLI.

---

## 4. Postgres Index Schema

A single denormalized table optimized for the primary retrieval query: "give me the N most recent fixtures for service X, endpoint Y."

```sql
CREATE TABLE fixtures_index (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service         TEXT NOT NULL,
  method          TEXT NOT NULL,
  path            TEXT NOT NULL,
  endpoint_key    TEXT NOT NULL,
  content_hash    TEXT NOT NULL,
  s3_uri          TEXT NOT NULL,
  recorded_at     TIMESTAMPTZ NOT NULL,
  indexed_at      TIMESTAMPTZ DEFAULT now(),
  tags            JSONB DEFAULT '{}',
  schema_ver      TEXT DEFAULT 'v1',
  stale           BOOLEAN DEFAULT FALSE
);
```

### Indexes

```sql
CREATE INDEX idx_fixtures_lookup ON fixtures_index (service, endpoint_key, recorded_at DESC);
CREATE INDEX idx_fixtures_content_hash ON fixtures_index (content_hash);
```

### content_hash

SHA-256 of the canonical fixture body (request + stubs). Used by the Indexer for deduplication: if the same content_hash exists within a configurable window (default: 6 hours), the row is skipped or its recorded_at is updated. This prevents unbounded growth from identical repeated requests.

### tags (JSONB)

Optional metadata for future filtering. Examples: scenario name, user segment, feature flags active during recording. Not required for V0 retrieval but the column is present for forward compatibility.

---

## 5. Sampling Strategy

Sampling operates at two layers, each serving a different purpose:

- **SDK-level** (protects the live service): A configurable rate gate (`SIM_SAMPLE_RATE` env var, default 1.0 = 100%). The middleware generates a random float per request; if above the rate, capture is skipped. For V0 dogfooding on a low-traffic internal monolith, default to 100%. This is a safety valve for production use.
- **Indexer-level** (protects storage): Content-hash deduplication with a time window. If the Indexer sees a fixture whose content_hash already exists in the index within the last N hours (configurable, default 6h), it skips the insert. Additionally, a hard ceiling of `max_fixtures_per_endpoint_per_day` (default: 200) prevents runaway growth.

Both layers are simple configuration knobs, not complex algorithms. They can be tuned during dogfooding.

---

## 6. Task Assignments

| ID  | Task                        | Assignee | Day | File                                  |
|-----|-----------------------------|----------|-----|---------------------------------------|
| 3.1 | S3 Key + Sampling (Daemon)  | Eng A    | 1   | `sim_sdk/fixture_uploader.py`         |
| 3.2 | SQS Infrastructure Setup    | Eng B    | 1   | `infra/` (Terraform or manual)        |
| 3.3 | Postgres Schema + Migrations| Eng B    | 1   | `fixture_service/migrations/`         |
| 3.4 | Indexer Service             | Eng A    | 2–3 | `fixture_service/indexer.py`          |
| 3.5 | Retrieval API               | Eng B    | 2–3 | `fixture_service/api.py`              |
| 3.6 | CLI (sim-fetch)             | Eng B    | 3–4 | `sim_runner/fetch_cli.py`             |
| 3.7 | Integration + Verification  | Both     | 4–5 | `tests/`                              |

---

## 7. Task Details

### 7.1 — Task 3.1: S3 Structured Key + SDK Sampling

**Assignee:** Eng A
**File:** `sim_sdk/fixture_uploader.py`

**Objective:** Modify the existing Fixture Daemon to: (a) generate structured S3 keys instead of flat keys, and (b) support SDK-level sampling via `SIM_SAMPLE_RATE`.

#### S3 Key Generation

```python
def build_s3_key(service, method, path, fixture_id, recorded_at):
    raw = f"{method}_{path}".lower().replace("/", "_")
    endpoint_key = re.sub(r"_+", "_", raw).strip("_")
    date_str = recorded_at.strftime("%Y-%m-%d")
    return f"fixtures/{service}/{endpoint_key}/{date_str}/{fixture_id}.json"
```

#### Sampling Logic (Middleware Addition)

```python
import random, os
SAMPLE_RATE = float(os.environ.get("SIM_SAMPLE_RATE", "1.0"))
if random.random() > SAMPLE_RATE: return  # skip capture
```

#### Exit Criteria

- Fixtures uploaded to S3 with prefix: `fixtures/{service}/{endpoint_key}/{date}/{fixture_id}.json`
- `SIM_SAMPLE_RATE=0.5` results in approximately 50% of requests being captured (verified over 100+ requests)
- `SIM_SAMPLE_RATE=1.0` (default) captures all requests — backward compatible with Phase 2
- Existing fixture JSON format unchanged — no breaking changes to stubs or request payloads
- Fixture ID (UUID) is generated at recording time and used as both the filename and the primary key for indexing

---

### 7.2 — Task 3.2: SQS Infrastructure Setup

**Assignee:** Eng B
**Files:** `infra/` (Terraform, CloudFormation, or manual AWS console)

**Objective:** Create the SQS queue and configure S3 event notifications so that every new fixture upload generates a message the Indexer can consume.

#### Components

- **SQS Queue:** `sim-fixtures-index-queue`. Standard queue (not FIFO — ordering is not required; the Indexer is idempotent). Visibility timeout: 60 seconds. Message retention: 4 days. Dead-letter queue: `sim-fixtures-index-dlq` after 3 failed receives.
- **S3 Event Notification:** On the fixtures bucket, configure `s3:ObjectCreated:*` events with prefix filter `fixtures/` to push to the SQS queue. The message body contains the S3 key, bucket name, object size, and event timestamp.
- **IAM:** SQS queue policy allows the S3 bucket to send messages. Indexer pod's service account has `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on the queue, plus `s3:GetObject` on the fixtures prefix.

#### Exit Criteria

- Uploading a fixture to `s3://bucket/fixtures/...` generates an SQS message within 5 seconds
- SQS message contains the S3 key, bucket, and event timestamp
- Dead-letter queue exists and captures messages that fail 3 times
- IAM permissions are minimal — Indexer can read SQS + read S3 fixtures prefix only
- Verified manually: upload a test file, confirm message appears in SQS, confirm DLQ is empty

---

### 7.3 — Task 3.3: Postgres Schema + Migrations

**Assignee:** Eng B
**Files:** `fixture_service/migrations/001_create_fixtures_index.sql`

**Objective:** Create the `fixtures_index` table and indexes in Postgres. Use a migration tool (Alembic, Flyway, or raw SQL files — team preference).

#### Schema

As defined in Section 4 of this document. The table, both indexes (lookup + content_hash), and a read-only role for the Retrieval API.

#### Exit Criteria

- Table `fixtures_index` exists with all columns from Section 4
- Composite index on `(service, endpoint_key, recorded_at DESC)` is verified via EXPLAIN on the retrieval query
- Content-hash index exists for dedup lookups
- Migration is idempotent (can be re-run without error)
- Read-only role for the Retrieval API — it cannot write to the index

---

### 7.4 — Task 3.4: Indexer Service

**Assignee:** Eng A
**File:** `fixture_service/indexer.py`

**Objective:** A long-running Python process (k8s Deployment, single replica for V0) that polls SQS for new fixture notifications, downloads the fixture from S3, extracts metadata, performs dedup, and writes an index row to Postgres.

#### Processing Loop

```python
while True:
    messages = sqs.receive_message(MaxNumberOfMessages=10, WaitTimeSeconds=20)
    for msg in messages:
        s3_key = parse_s3_event(msg)
        fixture = download_and_parse(s3_key)
        content_hash = sha256(canonicalize(fixture))
        if not is_duplicate(content_hash, window_hours=6):
            insert_index_row(fixture, s3_key, content_hash)
        sqs.delete_message(msg)
```

#### Deduplication Logic

Before inserting, query: `SELECT 1 FROM fixtures_index WHERE content_hash = %s AND recorded_at > now() - interval '6 hours'`. If a row exists, skip the insert (or optionally update `recorded_at` to reflect the most recent occurrence). The 6-hour window is configurable via `DEDUP_WINDOW_HOURS`.

#### Metadata Extraction

The Indexer parses the fixture JSON to extract: method, path, service name (from S3 prefix), recorded_at (from fixture or S3 event timestamp). It computes endpoint_key using the same slugify logic as the daemon (Task 3.1). Tags are extracted from an optional tags field in the fixture JSON if present.

#### Error Handling

- **Malformed fixture JSON:** Log error, delete SQS message (don't retry garbage). Emit a metric (`indexer_parse_errors`).
- **Postgres unavailable:** Don't delete SQS message. It becomes visible again after visibility timeout. After 3 failures, it goes to DLQ.
- **S3 download failure:** Same as Postgres — don't delete, let SQS retry.
- **Duplicate message (SQS at-least-once):** The content_hash dedup makes this idempotent. Inserting the same row twice is harmless (upsert or skip).

#### Exit Criteria

- Indexer starts, polls SQS, and processes messages end-to-end (S3 event → download → parse → insert)
- Deduplication: uploading the same fixture twice within 6 hours results in one index row (not two)
- Malformed fixtures are logged and discarded (no infinite retry)
- Postgres outage causes message to remain in SQS (retried on recovery)
- After 3 failures, message lands in DLQ
- Metrics emitted: `indexer_messages_processed`, `indexer_rows_inserted`, `indexer_duplicates_skipped`, `indexer_parse_errors`
- Verified: upload 10 fixtures via daemon, confirm 10 rows in Postgres (minus any deduped)

---

### 7.5 — Task 3.5: Retrieval API

**Assignee:** Eng B
**File:** `fixture_service/api.py`

**Objective:** A lightweight FastAPI service that queries the Postgres index and returns fixture manifests (lists of S3 URIs + metadata). Runs as a k8s Deployment.

#### Endpoints

**GET /fixtures**
Query parameters: `service` (required), `endpoint_key` (required), `limit` (default 50, max 500), `since` (ISO timestamp, optional — only return fixtures recorded after this time), `tags` (JSON string, optional — JSONB contains filter).

Returns a JSON manifest:

```json
{
  "service": "pricing-api",
  "endpoint_key": "post_quote",
  "count": 42,
  "fixtures": [
    {
      "id": "uuid",
      "s3_uri": "s3://bucket/fixtures/...",
      "recorded_at": "2026-03-21T14:30:00Z",
      "content_hash": "abc123...",
      "tags": {}
    }
  ]
}
```

**POST /fixtures/download**
Accepts a manifest (list of S3 URIs) and returns a zip archive of the fixture files. Used by the CLI to bulk-download fixtures for local replay. Alternative: returns pre-signed S3 URLs so the client can download directly.

**GET /fixtures/endpoints**
Returns a list of distinct (service, endpoint_key) pairs in the index. Useful for discovery: "what endpoints have recorded fixtures?"

**GET /health**
Liveness/readiness probe. Returns 200 if Postgres is reachable.

#### Design Notes

- Results are always ordered by `recorded_at DESC` (most recent first). The caller gets the freshest fixtures by default.
- The API uses a read-only Postgres connection. It cannot modify the index.
- Pre-signed S3 URLs (for `/fixtures/download`) have a short TTL (15 minutes). The CLI must download promptly.

#### Exit Criteria

- `GET /fixtures` returns correct fixtures for a given service + endpoint_key, ordered by `recorded_at DESC`
- Limit and since filters work correctly
- `GET /fixtures/endpoints` returns distinct endpoints with fixture counts
- `POST /fixtures/download` returns a zip (or pre-signed URLs) for the requested fixtures
- API returns 400 on missing required parameters, 404 on unknown service/endpoint
- Health endpoint returns 200 when Postgres is reachable, 503 otherwise
- Response time < 200ms for typical queries (50 fixtures, indexed lookup)

---

### 7.6 — Task 3.6: CLI (sim-fetch)

**Assignee:** Eng B
**File:** `sim_runner/fetch_cli.py`

**Objective:** A CLI wrapper around the Retrieval API that fetches fixtures and stages them locally in the format the replay runner expects. Used by engineers for local development and by CI for automated replay.

#### Interface

```bash
sim-fetch --service pricing-api --endpoint post_quote \
          --limit 50 --output-dir ./fixtures/quote \
          [--since 2026-03-20] [--api-url http://fixture-service:8000]
```

#### Behavior

1. Calls `GET /fixtures` to get the manifest.
2. Calls `POST /fixtures/download` (or uses pre-signed URLs) to download fixture files.
3. Writes fixtures to `--output-dir` with the same filename convention as Phase 2.
4. Prints a summary: `"Fetched 42 fixtures for POST /quote (most recent: 2026-03-21T14:30:00Z)."`
5. Exit code 0 on success, 1 on API error or no fixtures found.

#### CI Integration Pattern

In CI, the sim-fetch command runs before sim-replay. The pipeline is:

```
sim-fetch (get fixtures from cloud) → sim-replay (replay against candidate) → sim-verify (check results)
```

sim-fetch replaces the local fixture directory that Phase 2 assumed was already on disk.

#### Exit Criteria

- sim-fetch downloads fixtures from the Retrieval API and writes them to the specified output directory
- Output directory structure is compatible with sim-replay from Phase 2 (no changes needed to the runner)
- Handles 0 fixtures gracefully (prints warning, exits 1)
- Handles API errors gracefully (prints error, exits 1)
- `--since` flag correctly filters to recent fixtures only
- Works both locally (engineer running from laptop) and in CI (running in a container)

---

### 7.7 — Task 3.7: Integration + Verification

**Assignee:** Both
**Files:** `tests/`

**Objective:** End-to-end verification that the full pipeline works: daemon uploads fixture to S3 → SQS notification → Indexer processes and writes to Postgres → Retrieval API returns the fixture → CLI downloads and stages it → replay runner can use it.

#### Test Scenarios

1. **Happy path:** Record 5 fixtures for POST /quote, verify all 5 are indexable and retrievable via API and CLI.
2. **Deduplication:** Upload the same fixture body twice within 6 hours, verify only 1 index row exists.
3. **Freshness ordering:** Upload fixtures at different times, verify retrieval returns most-recent-first.
4. **Multi-endpoint:** Upload fixtures for 3 different endpoints, verify retrieval correctly scopes by endpoint_key.
5. **Replay compatibility:** Run sim-fetch → sim-replay → sim-verify end-to-end using fixtures sourced from S3 (not local disk).
6. **Sampling verification:** With `SIM_SAMPLE_RATE=0.5`, confirm approximately 50% capture rate over 100+ requests.
7. **Failure modes:** Indexer down → messages queue in SQS. Postgres down → API returns 503. S3 unavailable → CLI reports error.

#### Exit Criteria

- Full pipeline works end-to-end: record → upload → index → retrieve → replay
- Fixtures fetched from S3 via sim-fetch produce identical replay results as local fixtures from Phase 2
- Dedup, ordering, and multi-endpoint scoping all verified
- Failure modes handled gracefully (no data loss, no silent failures)

---

## 8. Schedule

| Day | Eng A | Eng B |
|-----|-------|-------|
| 1   | 3.1 (daemon S3 key + sampling) | 3.2 (SQS infra) + 3.3 (Postgres schema) |
| 2   | 3.4 Indexer (start: SQS consumer + S3 download + parse) | 3.5 Retrieval API (start: /fixtures + /fixtures/endpoints) |
| 3   | 3.4 Indexer (finish: dedup + error handling + metrics) | 3.5 API (finish: download endpoint) + 3.6 CLI (start) |
| 4   | 3.7 Integration (start) | 3.6 CLI (finish) + 3.7 Integration (start) |
| 5   | 3.7 E2E verification, failure modes, replay compat | 3.7 E2E verification, failure modes, replay compat |

---

## 9. Not Included in Phase 3

- Diff engine (baseline vs. candidate) — Phase 4
- Docker orchestration (runner building baseline + candidate containers) — Phase 4
- Smart fixture selection (coverage-aware sampling, change-focused selection) — post-V0
- TTL automation (S3 lifecycle policies for auto-expiry) — can be added any time, low priority
- Staleness warnings (alert when fixtures are older than N days) — V0.1
- LLM-based diff explanation — post-V0
- Multi-service fixture correlation — V1

---

## 10. Phase 3 Exit Criteria

1. Fixture Daemon uploads to S3 with structured prefix keys (service/endpoint/date/id).
2. SDK sampling (`SIM_SAMPLE_RATE`) controls capture rate at the middleware level.
3. S3 object-creation events flow to SQS within seconds of upload.
4. Indexer consumes SQS, parses fixtures, deduplicates by content hash, and writes to Postgres.
5. Retrieval API returns fixture manifests filtered by service + endpoint, ordered by recency.
6. sim-fetch CLI downloads fixtures and stages them in a format compatible with sim-replay.
7. End-to-end pipeline verified: record → upload → index → retrieve → replay produces same results as Phase 2 local replay.
8. Failure modes handled: Indexer retries on transient errors, DLQ catches poison messages, API returns appropriate error codes.

---

## 11. File Locations

| File | Purpose | Package |
|------|---------|---------|
| `sim_sdk/fixture_uploader.py` | 3.1 S3 key + sampling | sim_sdk |
| `infra/` | 3.2 SQS + S3 event config | infra |
| `fixture_service/migrations/` | 3.3 Postgres schema | fixture_service |
| `fixture_service/indexer.py` | 3.4 Indexer (SQS → Postgres) | fixture_service |
| `fixture_service/api.py` | 3.5 Retrieval API | fixture_service |
| `sim_runner/fetch_cli.py` | 3.6 CLI (sim-fetch) | sim_runner |
| `fixture_service/config.py` | Shared config (dedup window, limits) | fixture_service |
| `tests/` | 3.7 Integration tests | tests |
