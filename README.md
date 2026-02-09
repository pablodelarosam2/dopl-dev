# dopl-dev

A simulation platform for validating code changes by comparing baseline (main) vs candidate (PR) outputs using recorded fixtures and dependency stubs.

## Overview

dopl-dev helps catch "200 OK but wrong" bugs by:
1. Recording HTTP requests/responses and database queries during normal operation
2. Replaying those recordings during PR validation to create deterministic tests
3. Comparing outputs between baseline and candidate code to detect regressions

## Components

### sim_sdk (Python Package)

The SDK provides decorators and middleware to instrument your Flask application:

- **Flask Middleware**: Captures inbound requests and outbound responses
- **HTTP Interceptor**: Records/replays outbound HTTP calls (requests library)
- **DB Adapter**: Wraps psycopg2 for deterministic database access
- **Canonicalization**: Normalizes JSON for stable fingerprints
- **Redaction**: Removes PII before capture/comparison
- **SimClock**: Provides deterministic time for replay

## Installation

```bash
cd sim_sdk
pip install -e ".[dev]"
```

## Quick Start

### 1. Instrument your Flask app

```python
from flask import Flask, request, jsonify
from sim_sdk import sim_middleware, SimDB, patch_requests

app = Flask(__name__)
sim_middleware(app)
patch_requests()

db = SimDB(dsn="postgresql://localhost/mydb")

@app.route("/quote", methods=["POST"])
def quote():
    data = request.json
    products = db.query(
        "SELECT price FROM products WHERE sku = ANY(%s)",
        [data["items"]]
    )
    subtotal = sum(p["price"] for p in products)
    tax = subtotal * 0.08875
    return jsonify({"subtotal": subtotal, "tax": tax, "total": subtotal + tax})
```

### 2. Record mode (capture real interactions)

```bash
SIM_MODE=record SIM_STUB_DIR=./stubs python app.py
curl -X POST http://localhost:5000/quote -H "Content-Type: application/json" -d '{"items": ["SKU1", "SKU2"]}'
```

### 3. Replay mode (deterministic execution)

```bash
SIM_MODE=replay SIM_STUB_DIR=./stubs python app.py
# DB queries return recorded results, no real connections needed
```

## Environment Variables

| Variable | Values | Description |
|----------|--------|-------------|
| `SIM_MODE` | `off`, `record`, `replay` | Operating mode |
| `SIM_RUN_ID` | UUID string | Unique identifier for this simulation run |
| `SIM_STUB_DIR` | Path | Directory for stub files |

## Configuration (sim.yaml)

```yaml
service:
  name: my-service
  port: 8080

redaction:
  jsonpaths:
    - "$.user.email"
    - "$.payment.card_number"

ignore:
  jsonpaths:
    - "$.request_id"
    - "$.timestamp"

tolerances:
  money_paths: ["$.total", "$.subtotal"]
  money_abs: 0.01
```

## Running Tests

```bash
cd sim_sdk
pytest tests/ -v
```

## License

MIT
