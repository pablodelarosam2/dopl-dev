"""
Demo Flask application using sim_sdk.

This app demonstrates:
- Flask middleware for request/response capture
- Database queries with record/replay
- HTTP client calls with record/replay

Run with different SIM_MODE values:
- off: Normal operation
- record: Capture all interactions to stubs
- replay: Use stubs instead of real services
"""

import os
from flask import Flask, jsonify, request

# Import sim_sdk components
from sim_sdk import (
    SimDB,
    patch_requests,
    sim_clock,
    sim_middleware,
)

# Create Flask app
app = Flask(__name__)

# Register simulation middleware
sim_middleware(app)

# Patch requests library for HTTP interception
patch_requests()

# Database connection (use env var or default)
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://localhost/demo_db",
)

# Create database wrapper
# In replay mode, this won't actually connect
db = SimDB(dsn=DATABASE_URL)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "sim_mode": os.environ.get("SIM_MODE", "off"),
        "timestamp": sim_clock.now().isoformat(),
    })


@app.route("/quote", methods=["POST"])
def quote():
    """
    Calculate a price quote.

    Expected body:
    {
        "user_id": 123,
        "items": [
            {"sku": "PRODUCT-A", "qty": 2},
            {"sku": "PRODUCT-B", "qty": 1}
        ]
    }

    Returns:
    {
        "subtotal": 59.97,
        "tax": 5.32,
        "total": 65.29,
        "items": [...]
    }
    """
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    user_id = data.get("user_id")
    items = data.get("items", [])

    if not items:
        return jsonify({"error": "At least one item required"}), 400

    # Get product prices from database
    skus = [item["sku"] for item in items]
    products = db.query(
        "SELECT sku, price, name FROM products WHERE sku = ANY(%s)",
        (skus,),
    )

    # Create lookup map
    product_map = {p["sku"]: p for p in products}

    # Calculate line items
    line_items = []
    subtotal = 0.0

    for item in items:
        sku = item["sku"]
        qty = item.get("qty", 1)

        product = product_map.get(sku)
        if not product:
            return jsonify({"error": f"Product not found: {sku}"}), 404

        line_total = float(product["price"]) * qty
        subtotal += line_total

        line_items.append({
            "sku": sku,
            "name": product["name"],
            "qty": qty,
            "unit_price": float(product["price"]),
            "line_total": round(line_total, 2),
        })

    # Get tax rate for user's region
    user = db.query_one(
        "SELECT region FROM users WHERE id = %s",
        (user_id,),
    )

    if user:
        tax_info = db.query_one(
            "SELECT rate FROM tax_rates WHERE region = %s",
            (user["region"],),
        )
        tax_rate = float(tax_info["rate"]) if tax_info else 0.0
    else:
        tax_rate = 0.08875  # Default tax rate

    # Calculate totals
    tax = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax, 2)

    return jsonify({
        "user_id": user_id,
        "items": line_items,
        "subtotal": round(subtotal, 2),
        "tax_rate": tax_rate,
        "tax": tax,
        "total": total,
        "quoted_at": sim_clock.now().isoformat(),
    })


@app.route("/user/<int:user_id>", methods=["GET"])
def get_user(user_id: int):
    """Get user information."""
    user = db.query_one(
        "SELECT id, name, email, region FROM users WHERE id = %s",
        (user_id,),
    )

    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify(user)


@app.route("/products", methods=["GET"])
def list_products():
    """List all products."""
    products = db.query("SELECT sku, name, price FROM products ORDER BY name")

    return jsonify({
        "products": products,
        "count": len(products),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    print(f"Starting demo app on port {port}")
    print(f"SIM_MODE: {os.environ.get('SIM_MODE', 'off')}")
    print(f"SIM_STUB_DIR: {os.environ.get('SIM_STUB_DIR', 'not set')}")

    app.run(host="0.0.0.0", port=port, debug=debug)
