# Flask Demo — sim_sdk Phase 1 Integration Test

Manual integration test for all Phase 1 SDK primitives (`@sim_trace`, `sim_capture`, `sim_db`) in a Flask application.

## Setup

```bash
cd examples/flask_app
pip install -r requirements.txt
```

## How It Works

The app has 3 endpoints that exercise the SDK primitives:

| Endpoint | Primitives | Purpose |
|----------|-----------|---------|
| `GET /health` | None | Shows current SIM_MODE |
| `POST /quote` | `@sim_trace` + `sim_capture` + `sim_db` | Full pipeline |
| `GET /user/<id>` | `@sim_trace` + `sim_db` | Simpler trace + db |

All database calls use an in-memory `FakeDB` (no real DB driver needed).

## Testing: Record → Replay → Compare

### Step 1: Record

```bash
rm -rf .sim/fixtures
SIM_MODE=record python3 app.py
```

In another terminal:

```bash
# Calculate a quote (exercises all 3 primitives)
curl -s -X POST http://localhost:5000/quote \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": [{"sku": "WIDGET-A", "qty": 2}, {"sku": "GADGET-B", "qty": 1}]}'

# Get user info (exercises @sim_trace + sim_db)
curl -s http://localhost:5000/user/1
```

**What to observe in the server terminal:**
- `[REQUEST]` — SimContext created per request with mode=record
- `[FakeDB]` — Real database queries executing
- `[LOGIC]` — Business logic running
- `[SINK]` — Fixture files being written to disk
- `[RESPONSE]` — Request complete, stubs collected

Stop the server (Ctrl+C) and inspect the fixtures:

```bash
find .sim/fixtures -name "*.json" | sort
cat .sim/fixtures/calculate_quote/*.json | python3 -m json.tool
```

### Step 2: Replay

```bash
SIM_MODE=replay python3 app.py
```

Run the **exact same** curl commands:

```bash
curl -s -X POST http://localhost:5000/quote \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": [{"sku": "WIDGET-A", "qty": 2}, {"sku": "GADGET-B", "qty": 1}]}'

curl -s http://localhost:5000/user/1
```

**What to observe:**
- `[REQUEST]` prints appear (context still created)
- **NO** `[FakeDB]` prints (database never called)
- **NO** `[LOGIC]` prints (function body never executes)
- **NO** `[SINK]` prints (nothing recorded)
- Responses are **identical** to record mode

### Step 3: Test with different args (stub miss)

```bash
# Different qty → different fingerprint → no fixture
curl -s -X POST http://localhost:5000/quote \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": [{"sku": "WIDGET-A", "qty": 3}]}'
```

This should return a 500 error with `SimStubMissError` — the fingerprint for qty=3 doesn't match any recorded fixture.

### Step 4: Off mode

```bash
python3 app.py   # SIM_MODE defaults to "off"
```

Same curl commands work normally. No fixtures recorded, no replay — pure passthrough.

## Automated Validation Script

```bash
#!/bin/bash
set -e

echo "=== Step 1: Record ==="
rm -rf .sim/fixtures
SIM_MODE=record python3 app.py &
PID=$!
sleep 2

RECORD_QUOTE=$(curl -s -X POST http://localhost:5000/quote \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"items":[{"sku":"WIDGET-A","qty":2}]}')
RECORD_USER=$(curl -s http://localhost:5000/user/1)
kill $PID; wait $PID 2>/dev/null

echo "Recorded quote: $RECORD_QUOTE"
echo "Recorded user:  $RECORD_USER"
echo "Fixtures:"
find .sim/fixtures -name "*.json" | sort

echo ""
echo "=== Step 2: Replay ==="
SIM_MODE=replay python3 app.py &
PID=$!
sleep 2

REPLAY_QUOTE=$(curl -s -X POST http://localhost:5000/quote \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"items":[{"sku":"WIDGET-A","qty":2}]}')
REPLAY_USER=$(curl -s http://localhost:5000/user/1)
kill $PID; wait $PID 2>/dev/null

echo "Replayed quote: $REPLAY_QUOTE"
echo "Replayed user:  $REPLAY_USER"

echo ""
echo "=== Step 3: Diff ==="
if [ "$RECORD_QUOTE" = "$REPLAY_QUOTE" ]; then
  echo "PASS: Quote responses match"
else
  echo "FAIL: Quote responses differ"
fi

if [ "$RECORD_USER" = "$REPLAY_USER" ]; then
  echo "PASS: User responses match"
else
  echo "FAIL: User responses differ"
fi
```

## Notes

- Flask is in this example's `requirements.txt`, **not** in sim-sdk's dependencies
- sim-sdk is framework-agnostic — the same primitives work with FastAPI, Django, or plain Python
- The `FakeDB` class has no dependency on any real database driver
