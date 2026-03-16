"""
Flask integration demo for sim_sdk — all four primitives in one flow.

Exercises every SDK primitive inside a single calculate_quote() call:
    @sim_trace  — function-level record/replay (wraps the whole function)
    sim_db      — database capture (products lookup, quote insert)
    sim_capture — transport-agnostic capture (tax rate computation)
    sim_http    — HTTP request capture (external shipping rate API)

Endpoints:
    POST /quote   — @sim_trace + sim_db + sim_capture + sim_http
    GET  /health  — health-check showing mode + metrics

Usage:
    # 1. Start the record-agent (in another terminal)
    # 2. Record mode — fixtures written to .sim/fixtures/
    SIM_MODE=record python3 app.py
    curl -s -X POST http://localhost:5050/quote \
      -H "Content-Type: application/json" \
      -d '{"user_id": 1, "items": [{"sku": "WIDGET-A", "qty": 2}]}'

    # 3. Replay mode — same response, zero I/O
    SIM_MODE=replay python3 app.py
    curl -s -X POST http://localhost:5050/quote \
      -H "Content-Type: application/json" \
      -d '{"user_id": 1, "items": [{"sku": "WIDGET-A", "qty": 2}]}'
"""

import atexit
import json
import os
import sqlite3
import uuid
from pathlib import Path

from flask import Flask, jsonify, request

from sim_sdk.context import SimContext, SimMode, set_context, clear_context, get_context
from sim_sdk.trace import sim_trace
from sim_sdk.capture import sim_capture
from sim_sdk.db import sim_db
from sim_sdk.http import sim_http
from sim_sdk.sink.record_sink import RecordSink


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SIM_MODE_STR = os.environ.get("SIM_MODE", "off").lower()
SERVICE_NAME = os.environ.get("DOPL_SERVICE", "flask-demo")
STUB_DIR = Path(".sim/fixtures")
DB_PATH = os.environ.get("DB_PATH", ":memory:")


# ---------------------------------------------------------------------------
# LocalFileSink — writes FixtureEvents to stub_dir as JSON files
# ---------------------------------------------------------------------------

class LocalFileSink(RecordSink):
    """Minimal sink that writes @sim_trace fixture events to disk.

    File layout matches what trace._read_fixture() expects:
        stub_dir/{qualname}/{input_fp[:16]}_{ordinal}.json
    """

    def __init__(self, stub_dir: Path):
        super().__init__(max_batch_events=1)
        self._stub_dir = stub_dir

    def _persist_batch(self, batch):
        for event in batch:
            safe_qualname = event.qualname.replace(".", "_")
            filename = f"{event.input_fingerprint[:16]}_{event.ordinal}.json"
            dirpath = self._stub_dir / safe_qualname
            dirpath.mkdir(parents=True, exist_ok=True)
            filepath = dirpath / filename
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(event.to_dict(), f, indent=2, default=str)


# ---------------------------------------------------------------------------
# ShippingClient — mock HTTP client for sim_http demo
# ---------------------------------------------------------------------------

class ShippingResponse:
    """Response-like object that sim_http can duck-type extract."""

    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._data = data
        self.headers = {"content-type": "application/json"}

    @property
    def text(self) -> str:
        return json.dumps(self._data)

    def json(self):
        return self._data


class ShippingClient:
    """Simulates calling https://api.shipping.example.com/v1/rate.

    Duck-typed HTTP client with .get() — works with sim_http without
    importing requests or httpx.
    """

    def get(self, url, **kwargs):
        payload = kwargs.get("json", {})
        region = payload.get("region", "US")
        total_weight = payload.get("total_weight", 1)

        rate_per_unit = 2.99
        surcharges = {"US-AK": 5.00, "US-HI": 5.00}
        surcharge = surcharges.get(region, 0.0)
        cost = round(rate_per_unit * total_weight + surcharge, 2)

        if total_weight >= 10:
            cost = 0.0

        return ShippingResponse(200, {
            "cost": cost,
            "region": region,
            "total_weight": total_weight,
            "carrier": "USPS",
        })


# ---------------------------------------------------------------------------
# SQLiteDB — thin wrapper giving sim_db the .query()/.execute() interface
# ---------------------------------------------------------------------------

class SQLiteDB:
    """Real SQLite database with the .query()/.execute() contract sim_db expects."""

    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._seed()

    def query(self, sql: str, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or [])
        if cur.description is None:
            return []
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def execute(self, sql: str, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or [])
        self.conn.commit()
        return {"rowcount": cur.rowcount, "lastrowid": cur.lastrowid}

    def _seed(self):
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                sku   TEXT PRIMARY KEY,
                name  TEXT NOT NULL,
                price REAL NOT NULL
            );
            INSERT OR IGNORE INTO products VALUES ('WIDGET-A', 'Premium Widget', 29.99);
            INSERT OR IGNORE INTO products VALUES ('GADGET-B', 'Super Gadget',   49.99);
            INSERT OR IGNORE INTO products VALUES ('TOOL-C',   'Pro Tool',       19.99);

            CREATE TABLE IF NOT EXISTS users (
                id     INTEGER PRIMARY KEY,
                name   TEXT NOT NULL,
                email  TEXT NOT NULL,
                region TEXT NOT NULL
            );
            INSERT OR IGNORE INTO users VALUES (1, 'Alice Johnson', 'alice@example.com', 'US-CA');
            INSERT OR IGNORE INTO users VALUES (2, 'Bob Smith',     'bob@example.com',   'US-NY');

            CREATE TABLE IF NOT EXISTS quotes (
                quote_id   TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                subtotal   REAL NOT NULL,
                tax        REAL NOT NULL,
                total      REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        self.conn.commit()


# ---------------------------------------------------------------------------
# Flask app + global instances
# ---------------------------------------------------------------------------

app = Flask(__name__)
db = SQLiteDB(DB_PATH)
shipping_client = ShippingClient()

sink = None
if SIM_MODE_STR == "record":
    STUB_DIR.mkdir(parents=True, exist_ok=True)
    sink = LocalFileSink(stub_dir=STUB_DIR)


# ---------------------------------------------------------------------------
# Flask hooks — per-request SimContext lifecycle
# ---------------------------------------------------------------------------

@app.before_request
def before_sim():
    mode = SimMode(SIM_MODE_STR)
    ctx = SimContext(
        mode=mode,
        run_id="demo-run",
        stub_dir=STUB_DIR if mode in (SimMode.RECORD, SimMode.REPLAY) else None,
        sink=sink,
    )
    ctx.start_new_request()
    set_context(ctx)


@app.after_request
def after_sim(response):
    clear_context()
    return response


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

@sim_trace
def calculate_quote(user_id: int, items: list):
    """Core business logic: look up products, get tax, get shipping, persist quote.

    Exercises all four primitives:
      - sim_db      read   (products + users lookup)
      - sim_capture        (tax rate computation — local side effect)
      - sim_http           (external shipping rate API — HTTP call)
      - sim_db      write  (INSERT quote row)
    """
    skus = [item["sku"] for item in items]
    placeholders = ",".join("?" for _ in skus)

    with sim_db(db, name="products") as sdb:
        products = sdb.query(
            f"SELECT sku, name, price FROM products WHERE sku IN ({placeholders})",
            skus,
        )

    product_map = {p["sku"]: p for p in products}

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

    with sim_capture("tax_service") as cap:
        if not cap.replaying:
            with sim_db(db, name="users") as sdb:
                rows = sdb.query(
                    "SELECT region FROM users WHERE id = ?",
                    [user_id],
                )
            region = rows[0]["region"] if rows else "US-CA"
            tax_rate = {"US-CA": 0.0725, "US-NY": 0.08875}.get(region, 0.0)
            cap.set_result({"region": region, "rate": tax_rate})
        tax_info = cap.result

    tax = round(subtotal * tax_info["rate"], 2)

    # --- sim_http: external shipping rate API --------------------------
    total_qty = sum(item.get("qty", 1) for item in items)
    with sim_http(shipping_client, name="shipping") as client:
        ship_resp = client.get(
            "https://api.shipping.example.com/v1/rate",
            json={"region": tax_info["region"], "total_weight": total_qty},
        )
    shipping = ship_resp.json().get("cost", 0.0)

    total = round(subtotal + tax + shipping, 2)
    quote_id = str(uuid.uuid4())[:8]

    with sim_db(db, name="quotes") as sdb:
        sdb.execute(
            "INSERT INTO quotes (quote_id, user_id, subtotal, tax, total) VALUES (?, ?, ?, ?, ?)",
            [quote_id, user_id, subtotal, tax, total],
        )

    return {
        "quote_id": quote_id,
        "user_id": user_id,
        "items": line_items,
        "subtotal": round(subtotal, 2),
        "tax_rate": tax_info["rate"],
        "tax_region": tax_info["region"],
        "tax": tax,
        "shipping": shipping,
        "total": total,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "sim_mode": SIM_MODE_STR,
        "stub_dir": str(STUB_DIR),
        "sink": type(sink).__name__ if sink else "None",
    })


@app.route("/quote", methods=["POST"])
def quote():
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


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

def _shutdown():
    if sink is not None:
        print("\n[SHUTDOWN] Flushing sink")
        sink.close()


atexit.register(_shutdown)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[APP] SIM_MODE:   {SIM_MODE_STR}")
    print(f"[APP] SERVICE:    {SERVICE_NAME}")
    print(f"[APP] STUB_DIR:   {STUB_DIR}")
    print(f"[APP] DB_PATH:    {DB_PATH}")
    print(f"[APP] Sink:       {type(sink).__name__ if sink else 'None'}")
    print()
    app.run(port=int(os.environ.get("PORT", "5050")), debug=False)
