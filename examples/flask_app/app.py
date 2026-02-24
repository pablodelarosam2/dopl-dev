"""
Manual Flask integration test for sim_sdk Phase 1 primitives.

Demonstrates all three SDK primitives in a real Flask application:
- @sim_trace   — function-level record/replay
- sim_capture  — transport-agnostic dependency capture
- sim_db       — database query capture

Usage:
    SIM_MODE=record python3 app.py   # Record fixtures to .sim/fixtures/
    SIM_MODE=replay python3 app.py   # Replay from recorded fixtures
    python3 app.py                    # Off mode (normal execution)
"""

import json
import os
from pathlib import Path

from flask import Flask, g, jsonify, request

from sim_sdk.context import SimContext, SimMode, set_context, clear_context, get_context
from sim_sdk.trace import sim_trace, _fixture_key
from sim_sdk.capture import sim_capture
from sim_sdk.db import sim_db
from sim_sdk.sink import RecordSink
from sim_sdk.fixture.schema import FixtureEvent


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STUB_DIR = Path(".sim/fixtures")
SIM_MODE_STR = os.environ.get("SIM_MODE", "off").lower()


# ---------------------------------------------------------------------------
# PrintSink — demo sink that prints and writes fixture files
# ---------------------------------------------------------------------------

class PrintSink(RecordSink):
    """Sink that prints every recorded artifact and writes it to disk.

    Handles both sink protocols used by the SDK:
    - emit(event)      — called by @sim_trace (inherited from RecordSink)
    - write(key, data) — called by sim_capture() and sim_db()
    """

    def __init__(self, stub_dir: Path):
        super().__init__(max_batch_events=1)  # Flush after every emit
        self.stub_dir = stub_dir
        self.stub_dir.mkdir(parents=True, exist_ok=True)

    def emit(self, event: FixtureEvent) -> None:
        """Override to log buffer activity before delegating to RecordSink."""
        buf = self._buffer
        print(f"\n[BUFFER] emit() called — buffer size BEFORE: {len(buf.buffer)}")
        print(f"  event.qualname: {event.qualname}")
        print(f"  event.input_fp: {event.input_fingerprint[:16]}...")
        super().emit(event)  # appends to buffer, then flushes if batch full
        print(f"[BUFFER] emit() done  — buffer size AFTER:  {len(buf.buffer)}")

    def flush(self) -> None:
        """Override to log drain activity."""
        buf = self._buffer
        count = len(buf.buffer)
        if count > 0:
            print(f"\n[BUFFER] flush() draining {count} event(s) from buffer...")
        super().flush()
        if count > 0:
            print(f"[BUFFER] flush() complete — buffer empty: {len(buf.buffer) == 0}")

    def _persist_batch(self, batch: list) -> None:
        """Called by RecordSink.emit() -> flush() for ALL event types.

        All three primitives (@sim_trace, sim_capture, sim_db) now flow
        through emit() → buffer → _persist_batch(). Events with a
        storage_key use that as the file path; otherwise we compute
        from _fixture_key().
        """
        print(f"[BUFFER] _persist_batch() received {len(batch)} event(s) to write to disk")
        for event in batch:
            if event.storage_key:
                key = event.storage_key
            else:
                key = _fixture_key(event.qualname, event.input_fingerprint, event.ordinal)

            filepath = self.stub_dir / key
            filepath.parent.mkdir(parents=True, exist_ok=True)

            if event.storage_key and event.qualname.startswith("capture:"):
                # Capture events: write the replay-compatible format
                label = event.qualname.removeprefix("capture:")
                data = {
                    "type": "capture",
                    "label": label,
                    "ordinal": event.ordinal,
                    "result": event.output,
                }
            elif event.storage_key and event.qualname.startswith("db:"):
                # DB events: write the replay-compatible format
                name = event.qualname.removeprefix("db:")
                data = {
                    "type": "db_query",
                    "name": name,
                    "sql": event.input.get("sql", ""),
                    "params": event.input.get("params"),
                    "sql_fingerprint": event.input_fingerprint.split(":")[0] if ":" in event.input_fingerprint else event.input_fingerprint,
                    "params_fingerprint": event.input_fingerprint.split(":")[1] if ":" in event.input_fingerprint else "",
                    "ordinal": event.ordinal,
                    "result": event.output,
                }
            else:
                # @sim_trace events: write the full event dict
                data = event.to_dict()

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

            print(f"\n{'='*60}")
            print(f"[SINK] {event.qualname} fixture recorded")
            print(f"  storage_key: {event.storage_key or '(computed)'}")
            print(f"  ordinal:     {event.ordinal}")
            print(f"  file:        {filepath}")
            if event.input:
                input_preview = json.dumps(event.input, default=str)[:80]
                print(f"  input:       {input_preview}")
            if event.output is not None:
                output_preview = json.dumps(event.output, default=str)[:80]
                print(f"  output:      {output_preview}")
            print(f"{'='*60}")


# ---------------------------------------------------------------------------
# FakeDB — in-memory database for the demo (no real driver needed)
# ---------------------------------------------------------------------------

class FakeDB:
    """In-memory fake database with .query() and .execute() methods.

    Pre-loaded with users, products, and tax_rates tables.
    Prints every query for visibility during the demo.
    """

    def __init__(self):
        self.tables = {
            "users": [
                {"id": 1, "name": "Alice Johnson", "email": "alice@example.com", "region": "US-CA"},
                {"id": 2, "name": "Bob Smith", "email": "bob@example.com", "region": "US-NY"},
            ],
            "products": [
                {"sku": "WIDGET-A", "name": "Premium Widget", "price": 29.99},
                {"sku": "GADGET-B", "name": "Super Gadget", "price": 49.99},
                {"sku": "TOOL-C", "name": "Pro Tool", "price": 19.99},
            ],
            "tax_rates": [
                {"region": "US-CA", "rate": 0.0725},
                {"region": "US-NY", "rate": 0.08875},
            ],
        }

    def query(self, sql, params=None):
        """Simple pattern-matching query — enough for the demo."""
        print(f"  [FakeDB] query: {sql}")
        if params:
            print(f"  [FakeDB] params: {params}")

        if "FROM products WHERE sku" in sql and params:
            skus = params[0] if isinstance(params, (list, tuple)) else [params]
            if isinstance(skus, (list, tuple)):
                rows = [p for p in self.tables["products"] if p["sku"] in skus]
            else:
                rows = [p for p in self.tables["products"] if p["sku"] == skus]
            print(f"  [FakeDB] -> {len(rows)} row(s)")
            return rows

        if "FROM users WHERE id" in sql and params:
            user_id = params[0] if isinstance(params, (list, tuple)) else params
            rows = [u for u in self.tables["users"] if u["id"] == user_id]
            print(f"  [FakeDB] -> {len(rows)} row(s)")
            return rows

        if "FROM tax_rates WHERE region" in sql and params:
            region = params[0] if isinstance(params, (list, tuple)) else params
            rows = [t for t in self.tables["tax_rates"] if t["region"] == region]
            print(f"  [FakeDB] -> {len(rows)} row(s)")
            return rows

        print(f"  [FakeDB] -> no matching pattern, returning []")
        return []

    def execute(self, sql, params=None):
        """Execute a write statement (not used in this demo)."""
        print(f"  [FakeDB] execute: {sql}")
        return None


# ---------------------------------------------------------------------------
# Flask app + global instances
# ---------------------------------------------------------------------------

app = Flask(__name__)
fake_db = FakeDB()
sink = PrintSink(STUB_DIR) if SIM_MODE_STR == "record" else None


# ---------------------------------------------------------------------------
# Flask hooks — per-request SimContext lifecycle
# ---------------------------------------------------------------------------

@app.before_request
def before_sim():
    """Initialize SimContext for each incoming request."""
    mode = SimMode(SIM_MODE_STR)
    ctx = SimContext(
        mode=mode,
        run_id="demo-run",
        stub_dir=STUB_DIR if mode != SimMode.OFF else None,
        sink=sink,
    )
    request_id = ctx.start_new_request()
    set_context(ctx)

    print(f"\n{'#'*60}")
    print(f"[REQUEST] {request.method} {request.path}")
    print(f"  mode:       {mode.value}")
    print(f"  request_id: {request_id}")
    print(f"  stub_dir:   {STUB_DIR}")
    print(f"{'#'*60}")


@app.after_request
def after_sim(response):
    """Clean up SimContext and flush sink after each request."""
    ctx = get_context()
    print(f"\n[RESPONSE] status={response.status_code}")
    print(f"  collected_stubs: {len(ctx.collected_stubs)}")

    if sink is not None:
        sink.flush()

    clear_context()
    return response


# ---------------------------------------------------------------------------
# Business logic — decorated with SDK primitives
# ---------------------------------------------------------------------------

@sim_trace
def calculate_quote(user_id, items):
    """Core business logic: calculate a price quote.

    Uses sim_db() for database queries and sim_capture() for an
    external tax service call. All interactions are recorded/replayed.
    """
    print(f"\n  [LOGIC] calculate_quote(user_id={user_id}, items={items})")

    # 1. Query product prices from DB
    skus = [item["sku"] for item in items]
    with sim_db(fake_db, name="products_db") as db:
        products = db.query(
            "SELECT sku, name, price FROM products WHERE sku IN ($1)",
            [skus],
        )

    product_map = {p["sku"]: p for p in products}

    # 2. Build line items
    line_items = []
    subtotal = 0.0
    for item in items:
        product = product_map.get(item["sku"])
        if not product:
            return {"error": f"Product not found: {item['sku']}"}
        qty = item.get("qty", 1)
        line_total = round(product["price"] * qty, 2)
        subtotal += line_total
        line_items.append({
            "sku": item["sku"],
            "name": product["name"],
            "qty": qty,
            "unit_price": product["price"],
            "line_total": line_total,
        })

    # 3. Look up tax rate via "external service" (sim_capture)
    with sim_capture("tax_service_lookup") as cap:
        if not cap.replaying:
            print("  [LOGIC] Calling external tax service...")
            with sim_db(fake_db, name="tax_db") as db:
                user_rows = db.query(
                    "SELECT region FROM users WHERE id = $1",
                    [user_id],
                )
            region = user_rows[0]["region"] if user_rows else "US-CA"
            tax_rate = {"US-CA": 0.0725, "US-NY": 0.08875}.get(region, 0.0)
            cap.set_result({"region": region, "rate": tax_rate})
        tax_info = cap.result

    # 4. Calculate totals
    tax = round(subtotal * tax_info["rate"], 2)
    total = round(subtotal + tax, 2)

    result = {
        "user_id": user_id,
        "items": line_items,
        "subtotal": round(subtotal, 2),
        "tax_rate": tax_info["rate"],
        "tax_region": tax_info["region"],
        "tax": tax,
        "total": total,
    }
    print(f"  [LOGIC] Quote total: ${total}")
    return result


@sim_trace
def get_user_info(user_id):
    """Look up user information from the database."""
    print(f"\n  [LOGIC] get_user_info(user_id={user_id})")

    with sim_db(fake_db, name="users_db") as db:
        rows = db.query(
            "SELECT id, name, email, region FROM users WHERE id = $1",
            [user_id],
        )

    if not rows:
        return None
    return rows[0]


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    """Health check — shows current SIM_MODE."""
    return jsonify({
        "status": "ok",
        "sim_mode": SIM_MODE_STR,
    })


@app.route("/quote", methods=["POST"])
def quote():
    """Calculate a price quote (exercises all 3 SDK primitives)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    user_id = data.get("user_id", 1)
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "items required"}), 400

    result = calculate_quote(user_id, items)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/user/<int:user_id>")
def user(user_id):
    """Get user info (exercises @sim_trace + sim_db)."""
    result = get_user_info(user_id)
    if result is None:
        return jsonify({"error": "User not found"}), 404
    return jsonify(result)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[APP] Starting Flask demo app")
    print(f"[APP] SIM_MODE:  {SIM_MODE_STR}")
    print(f"[APP] STUB_DIR:  {STUB_DIR}")
    print(f"[APP] Sink:      {'PrintSink' if sink else 'None (off/replay mode)'}")
    print()
    app.run(port=int(os.environ.get("PORT", "5000")), debug=False)
