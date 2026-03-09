"""
Flask integration demo for sim_sdk with AgentSink transport.

Exercises all three SDK primitives against real SQLite and a mocked
external tax service, sending recorded events to the local record-agent
via the AgentSink background sender.

Endpoints:
    POST /quote   — @sim_trace + sim_db (read+write) + sim_capture
    GET  /health  — plain health-check showing mode + metrics

Usage:
    # 1. Start the record-agent (in another terminal)
    # 2. Start this app in record mode:
    SIM_MODE=record python3 app.py

    # 3. Hit the endpoint:
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
from sim_sdk.sink.agent_sink import AgentSink


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SIM_MODE_STR = os.environ.get("SIM_MODE", "off").lower()
AGENT_URL = os.environ.get("DOPL_AGENT_URL", "http://127.0.0.1:7777")
SERVICE_NAME = os.environ.get("DOPL_SERVICE", "flask-demo")
STUB_DIR = Path(".sim/fixtures")
DB_PATH = os.environ.get("DB_PATH", ":memory:")


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

sink = None
if SIM_MODE_STR == "record":
    sink = AgentSink(
        agent_url=AGENT_URL,
        service=SERVICE_NAME,
        flush_interval_s=0.5,
        max_batch_events=50,
    )


# ---------------------------------------------------------------------------
# Flask hooks — per-request SimContext lifecycle
# ---------------------------------------------------------------------------

@app.before_request
def before_sim():
    mode = SimMode(SIM_MODE_STR)
    ctx = SimContext(
        mode=mode,
        run_id="demo-run",
        stub_dir=STUB_DIR if mode == SimMode.REPLAY else None,
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
    """Core business logic: look up products, get tax, persist quote.

    Exercises:
      - sim_db  read  (products lookup)
      - sim_capture   (external tax service mock)
      - sim_db  write (INSERT quote row)
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
    total = round(subtotal + tax, 2)
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
        "total": total,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    metrics = sink.metrics.snapshot() if sink else {}
    return jsonify({
        "status": "ok",
        "sim_mode": SIM_MODE_STR,
        "agent_url": AGENT_URL,
        "sender_metrics": metrics,
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
        print(f"\n[SHUTDOWN] Flushing AgentSink — {sink.metrics}")
        sink.close()


atexit.register(_shutdown)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[APP] SIM_MODE:   {SIM_MODE_STR}")
    print(f"[APP] AGENT_URL:  {AGENT_URL}")
    print(f"[APP] SERVICE:    {SERVICE_NAME}")
    print(f"[APP] DB_PATH:    {DB_PATH}")
    print(f"[APP] Sink:       {'AgentSink' if sink else 'None'}")
    print()
    app.run(port=int(os.environ.get("PORT", "5050")), debug=False)
