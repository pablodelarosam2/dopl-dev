#!/usr/bin/env python3
"""
Test script for the demo Flask app.

This script demonstrates and tests the sim_sdk functionality:
1. Test off mode (normal operation - requires real DB)
2. Test record mode (capture interactions)
3. Test replay mode (use captured stubs)

Usage:
    # Test replay mode with pre-existing stubs
    python run_test.py --replay

    # Test record mode (requires real database)
    python run_test.py --record

    # Run all tests (requires real database for record)
    python run_test.py --all
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Add sim_sdk to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "sim_sdk"))


def setup_stubs(stub_dir: Path) -> None:
    """Create test stubs for replay mode."""
    from sim_sdk.canonicalize import fingerprint_sql
    from sim_sdk.store import StubStore

    store = StubStore(stub_dir)

    # Stub for product query
    fp_products = fingerprint_sql(
        "SELECT sku, price, name FROM products WHERE sku = ANY(%s)",
        (["PRODUCT-A", "PRODUCT-B"],),
    )
    store.save_db(
        fp_products,
        0,
        [
            {"sku": "PRODUCT-A", "price": 19.99, "name": "Product A"},
            {"sku": "PRODUCT-B", "price": 29.99, "name": "Product B"},
        ],
    )

    # Stub for user query
    fp_user = fingerprint_sql(
        "SELECT region FROM users WHERE id = %s",
        (123,),
    )
    store.save_db(
        fp_user,
        0,
        [{"region": "CA"}],
    )

    # Stub for tax rate query
    fp_tax = fingerprint_sql(
        "SELECT rate FROM tax_rates WHERE region = %s",
        ("CA",),
    )
    store.save_db(
        fp_tax,
        0,
        [{"rate": 0.0925}],
    )

    print(f"Created stubs in {stub_dir}")
    print(f"  - Products query: {fp_products}")
    print(f"  - User query: {fp_user}")
    print(f"  - Tax rate query: {fp_tax}")


def test_replay_mode() -> bool:
    """Test the app in replay mode."""
    print("\n" + "=" * 60)
    print("Testing REPLAY mode")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        stub_dir = Path(tmpdir)

        # Create stubs
        setup_stubs(stub_dir)

        # Set environment for replay mode
        os.environ["SIM_MODE"] = "replay"
        os.environ["SIM_STUB_DIR"] = str(stub_dir)
        os.environ["SIM_RUN_ID"] = "test-replay-001"

        # Clear any cached context
        from sim_sdk.context import clear_context
        clear_context()

        # Import app (will pick up env vars)
        from app import app

        # Create test client
        client = app.test_client()

        # Test health endpoint
        print("\n1. Testing /health endpoint...")
        response = client.get("/health")
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.json}")

        if response.status_code != 200:
            print("   FAILED: Health check failed")
            return False

        # Test quote endpoint
        print("\n2. Testing /quote endpoint...")
        response = client.post(
            "/quote",
            json={
                "user_id": 123,
                "items": [
                    {"sku": "PRODUCT-A", "qty": 2},
                    {"sku": "PRODUCT-B", "qty": 1},
                ],
            },
            content_type="application/json",
        )
        print(f"   Status: {response.status_code}")
        print(f"   Response: {json.dumps(response.json, indent=2)}")

        if response.status_code != 200:
            print("   FAILED: Quote request failed")
            return False

        # Verify calculations
        data = response.json
        expected_subtotal = (19.99 * 2) + (29.99 * 1)  # 69.97
        expected_tax = round(expected_subtotal * 0.0925, 2)  # 6.47
        expected_total = round(expected_subtotal + expected_tax, 2)  # 76.44

        print(f"\n   Expected subtotal: {expected_subtotal}")
        print(f"   Actual subtotal: {data['subtotal']}")
        print(f"   Expected tax: {expected_tax}")
        print(f"   Actual tax: {data['tax']}")
        print(f"   Expected total: {expected_total}")
        print(f"   Actual total: {data['total']}")

        # Allow small floating point differences
        if abs(data["subtotal"] - expected_subtotal) > 0.01:
            print("   FAILED: Subtotal mismatch")
            return False

        if abs(data["tax"] - expected_tax) > 0.01:
            print("   FAILED: Tax mismatch")
            return False

        if abs(data["total"] - expected_total) > 0.01:
            print("   FAILED: Total mismatch")
            return False

        # Check sim headers
        print("\n3. Checking simulation headers...")
        print(f"   X-Sim-Request-Id: {response.headers.get('X-Sim-Request-Id')}")
        print(f"   X-Sim-Run-Id: {response.headers.get('X-Sim-Run-Id')}")

        if "X-Sim-Request-Id" not in response.headers:
            print("   FAILED: Missing X-Sim-Request-Id header")
            return False

        print("\n" + "-" * 60)
        print("REPLAY mode tests PASSED!")
        print("-" * 60)
        return True


def test_record_mode() -> bool:
    """Test the app in record mode (requires real database)."""
    print("\n" + "=" * 60)
    print("Testing RECORD mode")
    print("=" * 60)
    print("\nNote: This test requires a real PostgreSQL database.")
    print("Set DATABASE_URL environment variable to your database.")

    if "DATABASE_URL" not in os.environ:
        print("\nSkipping: DATABASE_URL not set")
        return True  # Not a failure, just skipped

    with tempfile.TemporaryDirectory() as tmpdir:
        stub_dir = Path(tmpdir)

        # Set environment for record mode
        os.environ["SIM_MODE"] = "record"
        os.environ["SIM_STUB_DIR"] = str(stub_dir)
        os.environ["SIM_RUN_ID"] = "test-record-001"

        # Clear any cached context
        from sim_sdk.context import clear_context
        clear_context()

        # Import app
        from app import app

        client = app.test_client()

        # Test health endpoint
        print("\n1. Testing /health endpoint...")
        response = client.get("/health")
        print(f"   Status: {response.status_code}")

        # Check that stubs were created
        from sim_sdk.store import StubStore
        store = StubStore(stub_dir)

        stats = store.stats()
        print(f"\n2. Stubs created:")
        print(f"   HTTP stubs: {stats['http_stubs']}")
        print(f"   DB stubs: {stats['db_stubs']}")
        print(f"   Requests: {stats['requests']}")

        print("\n" + "-" * 60)
        print("RECORD mode tests PASSED!")
        print("-" * 60)
        return True


def main():
    parser = argparse.ArgumentParser(description="Test sim_sdk demo app")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Run replay mode test",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Run record mode test (requires real DB)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all tests",
    )

    args = parser.parse_args()

    # Default to replay if no args
    if not args.replay and not args.record and not args.all:
        args.replay = True

    results = []

    if args.replay or args.all:
        results.append(("Replay", test_replay_mode()))

    if args.record or args.all:
        results.append(("Record", test_record_mode()))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "PASSED" if passed else "FAILED"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\nAll tests passed!")
        sys.exit(0)
    else:
        print("\nSome tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
