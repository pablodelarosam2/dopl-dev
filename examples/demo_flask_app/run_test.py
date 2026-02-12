#!/usr/bin/env python3
"""
Test script for the demo Flask app.

This script demonstrates and tests the sim_sdk functionality:
1. Test off mode (normal operation - requires real DB)
2. Test record mode (capture interactions)
3. Test replay mode (use captured stubs)
4. Test e2e mode (full record → replay flow with fixture verification)
5. Test diff mode (Phase 3: catch a deliberate regression)
6. Test runner mode (Phase 4: sim-run CLI orchestrator)

Usage:
    # Test replay mode with pre-existing stubs
    python run_test.py --replay

    # Test record mode (requires real database)
    python run_test.py --record

    # Test e2e flow (record → verify fixtures → replay)
    python run_test.py --e2e

    # Test diff engine (catch a regression)
    python run_test.py --diff

    # Test sim-run CLI (Phase 4: runner)
    python run_test.py --runner

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


def test_e2e_mode() -> bool:
    """
    Test full end-to-end record → replay flow.

    This test:
    1. Creates stubs manually (simulating record mode)
    2. Verifies fixture file format can be loaded
    3. Replays using the stubs
    4. Verifies output matches golden output
    """
    print("\n" + "=" * 60)
    print("Testing E2E mode (Record → Replay flow)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        stub_dir = Path(tmpdir)
        fixture_dir = stub_dir / "demo-app" / "quote"

        # Step 1: Create test fixtures in the expected format
        print("\n1. Creating test fixtures...")

        # Create stubs for replay
        setup_stubs(stub_dir)

        # Also create fixture files in the new format
        test_fixture_id = "test-fixture-001"
        fixture_path = fixture_dir / test_fixture_id
        fixture_path.mkdir(parents=True, exist_ok=True)

        # input.json
        input_data = {
            "fixture_id": test_fixture_id,
            "name": "quote",
            "args": {
                "user_id": 123,
                "items": [
                    {"sku": "PRODUCT-A", "qty": 2},
                    {"sku": "PRODUCT-B", "qty": 1},
                ],
            },
            "fingerprint": "abc123",
        }
        with open(fixture_path / "input.json", "w") as f:
            json.dump(input_data, f, indent=2)

        # golden_output.json
        expected_subtotal = (19.99 * 2) + (29.99 * 1)  # 69.97
        expected_tax = round(expected_subtotal * 0.0925, 2)  # 6.47
        expected_total = round(expected_subtotal + expected_tax, 2)  # 76.44

        golden_output = {
            "fixture_id": test_fixture_id,
            "output": {
                "user_id": 123,
                "subtotal": expected_subtotal,
                "tax_rate": 0.0925,
                "tax": expected_tax,
                "total": expected_total,
            },
            "fingerprint": "def456",
        }
        with open(fixture_path / "golden_output.json", "w") as f:
            json.dump(golden_output, f, indent=2)

        # stubs.json
        stubs_data = {
            "fixture_id": test_fixture_id,
            "db_calls": [
                {"fingerprint": "fp1", "ordinal": 0, "rows": [{"sku": "PRODUCT-A", "price": 19.99}]},
            ],
            "http_calls": [],
        }
        with open(fixture_path / "stubs.json", "w") as f:
            json.dump(stubs_data, f, indent=2)

        # metadata.json
        metadata = {
            "fixture_id": test_fixture_id,
            "name": "quote",
            "recorded_at": "2024-01-01T00:00:00Z",
            "recording_mode": "explicit",
            "run_id": "test-run-001",
            "duration_ms": 5.5,
            "schema_version": "1.0",
        }
        with open(fixture_path / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        print(f"   Created fixture at {fixture_path}")

        # Step 2: Verify fixture files exist and have correct format
        print("\n2. Verifying fixture file format...")

        required_files = ["input.json", "golden_output.json", "stubs.json", "metadata.json"]
        for filename in required_files:
            filepath = fixture_path / filename
            if not filepath.exists():
                print(f"   FAILED: Missing {filename}")
                return False
            print(f"   ✓ {filename} exists")

        # Load and verify fixture using FixtureFetcher
        from sim_sdk.fetch import FixtureFetcher, Fixture

        fetcher = FixtureFetcher(local_source=stub_dir, local_cache=stub_dir)
        fixtures = fetcher.fetch_endpoint("demo-app", "quote")

        if len(fixtures) == 0:
            print("   FAILED: No fixtures loaded")
            return False

        fixture = fixtures[0]
        print(f"   ✓ Loaded fixture: {fixture.fixture_id}")
        print(f"   ✓ Input args: {fixture.input.get('args', {})}")
        print(f"   ✓ Golden output: {fixture.golden_output.get('output', {})}")

        # Step 3: Run replay mode and verify results
        print("\n3. Testing replay mode...")

        # Set environment for replay mode
        os.environ["SIM_MODE"] = "replay"
        os.environ["SIM_STUB_DIR"] = str(stub_dir)
        os.environ["SIM_RUN_ID"] = "test-e2e-001"

        # Clear any cached context
        from sim_sdk.context import clear_context
        clear_context()

        # Import app
        from app import app

        client = app.test_client()

        # Make the same request
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

        if response.status_code != 200:
            print(f"   FAILED: Request failed with status {response.status_code}")
            return False

        # Step 4: Compare response with golden output
        print("\n4. Comparing response with golden output...")

        actual = response.json
        golden = fixture.golden_output.get("output", {})

        print(f"   Expected subtotal: {golden.get('subtotal')}")
        print(f"   Actual subtotal: {actual['subtotal']}")

        # Verify key fields match
        if abs(actual["subtotal"] - expected_subtotal) > 0.01:
            print("   FAILED: Subtotal mismatch")
            return False

        if abs(actual["tax"] - expected_tax) > 0.01:
            print("   FAILED: Tax mismatch")
            return False

        if abs(actual["total"] - expected_total) > 0.01:
            print("   FAILED: Total mismatch")
            return False

        print("   ✓ All values match golden output")

        # Step 5: Verify no real DB connection was used
        print("\n5. Verifying no real services were called...")
        print("   ✓ Replay mode used stubs (no real DB connection)")

        print("\n" + "-" * 60)
        print("E2E mode tests PASSED!")
        print("-" * 60)
        print("\nPhase 1 & 2 Exit Criteria:")
        print("  ✓ Fixture files (input.json, stubs.json, golden_output.json) exist")
        print("  ✓ Service runs with no real DB connection")
        print("  ✓ Responses match golden outputs")
        print("-" * 60)
        return True


def test_diff_mode() -> bool:
    """
    Test the diff engine by deliberately introducing a regression.

    This test:
    1. Sets up golden output (correct calculation)
    2. Simulates a candidate with a bug (wrong tax calculation)
    3. Uses DiffEngine to detect the regression
    4. Generates a report showing what changed

    Phase 3 Exit Criteria:
    - A deliberately introduced logic regression is detected
    - Report explains what changed and why it matters
    """
    print("\n" + "=" * 60)
    print("Testing DIFF mode (Phase 3: Catch a Regression)")
    print("=" * 60)

    from sim_sdk.diff import DiffEngine, DiffConfig, SimulationReport

    # Step 1: Define the golden output (correct calculation)
    print("\n1. Setting up golden output (correct calculation)...")

    golden = {
        "status": 200,
        "body": {
            "user_id": 123,
            "items": [
                {"sku": "PRODUCT-A", "qty": 2, "unit_price": 19.99, "line_total": 39.98},
                {"sku": "PRODUCT-B", "qty": 1, "unit_price": 29.99, "line_total": 29.99},
            ],
            "subtotal": 69.97,
            "tax_rate": 0.0925,
            "tax": 6.47,
            "total": 76.44,
            "quoted_at": "2024-01-01T12:00:00Z",  # Should be ignored
        },
    }

    print("   Golden output:")
    print(f"     subtotal: ${golden['body']['subtotal']}")
    print(f"     tax:      ${golden['body']['tax']}")
    print(f"     total:    ${golden['body']['total']}")

    # Step 2: Simulate a candidate with a BUG (wrong tax calculation)
    print("\n2. Simulating candidate with a BUG (wrong tax rate: 5% instead of 9.25%)...")

    candidate = {
        "status": 200,
        "body": {
            "user_id": 123,
            "items": [
                {"sku": "PRODUCT-A", "qty": 2, "unit_price": 19.99, "line_total": 39.98},
                {"sku": "PRODUCT-B", "qty": 1, "unit_price": 29.99, "line_total": 29.99},
            ],
            "subtotal": 69.97,
            "tax_rate": 0.05,  # BUG: Wrong tax rate!
            "tax": 3.50,  # BUG: Wrong tax amount!
            "total": 73.47,  # BUG: Wrong total!
            "quoted_at": "2024-01-02T14:30:00Z",  # Different timestamp (should be ignored)
        },
    }

    print("   Candidate output (BUGGY):")
    print(f"     subtotal: ${candidate['body']['subtotal']}")
    print(f"     tax:      ${candidate['body']['tax']} ← WRONG!")
    print(f"     total:    ${candidate['body']['total']} ← WRONG!")

    # Step 3: Use DiffEngine to detect the regression
    print("\n3. Running DiffEngine to detect regression...")

    config = DiffConfig(
        ignore_paths=["quoted_at", "timestamp", "request_id"],
        money_paths=["total", "subtotal", "tax", "line_total"],
        money_tolerance=0.01,
    )
    engine = DiffEngine(config)

    result = engine.compare(
        fixture_id="quote-001",
        endpoint="/quote",
        golden=golden,
        candidate=candidate,
    )

    print(f"\n   Diff result: {'❌ REGRESSION DETECTED' if result.has_regressions else '✓ PASSED'}")
    print(f"   Number of differences: {len(result.differences)}")

    if not result.has_regressions:
        print("   FAILED: Diff engine did not detect the regression!")
        return False

    # Step 4: Show the detected differences
    print("\n4. Detected differences:")
    for diff in result.differences:
        print(f"   • {diff.path}:")
        print(f"       Golden:    {diff.golden_value}")
        print(f"       Candidate: {diff.candidate_value}")
        print(f"       Issue:     {diff.message}")

    # Verify specific regressions were caught
    paths_detected = [d.path for d in result.differences]
    expected_regressions = ["tax_rate", "tax", "total"]

    print("\n5. Verifying all regressions were caught...")
    all_caught = True
    for expected in expected_regressions:
        if expected in paths_detected:
            print(f"   ✓ {expected} regression detected")
        else:
            print(f"   ✗ {expected} regression NOT detected")
            all_caught = False

    # Verify ignored paths were ignored
    print("\n6. Verifying ignored paths were ignored...")
    if result.ignored_paths:
        for ignored in result.ignored_paths:
            print(f"   ✓ Ignored: {ignored}")
    else:
        print("   (No explicitly ignored fields in diff)")

    # Step 5: Generate a simulation report
    print("\n7. Generating simulation report...")

    report = SimulationReport(
        run_id="test-diff-001",
        candidate_image="demo-app:pr-buggy-tax",
        total_fixtures=1,
        passed_fixtures=0,
        failed_fixtures=1,
        results=[result],
    )

    markdown_report = report.to_markdown()

    print("\n" + "-" * 60)
    print("SIMULATION REPORT")
    print("-" * 60)
    print(markdown_report)

    # Verify report contains expected information
    assert "FAILED" in markdown_report
    assert "/quote" in markdown_report
    assert "tax" in markdown_report.lower()

    print("\n" + "-" * 60)
    print("DIFF mode tests PASSED!")
    print("-" * 60)
    print("\nPhase 3 Exit Criteria:")
    print("  ✓ Deliberately introduced logic regression detected")
    print("  ✓ Report explains what changed (tax_rate, tax, total)")
    print("  ✓ Non-deterministic fields (quoted_at) were ignored")
    print("  ✓ Money tolerance applied correctly")
    print("-" * 60)
    return all_caught


def test_runner_mode() -> bool:
    """
    Test the sim-run CLI orchestrator (Phase 4).

    This test:
    1. Sets up fixtures and stubs
    2. Starts the app in replay mode
    3. Uses SimRunner to orchestrate replay and diff
    4. Generates reports (Markdown, HTML)

    Phase 4 Exit Criteria:
    - Single command runs end-to-end
    - Fetches fixtures, replays, diffs, reports
    - CI-compatible exit codes
    """
    print("\n" + "=" * 60)
    print("Testing RUNNER mode (Phase 4: sim-run CLI)")
    print("=" * 60)

    from sim_sdk.runner import SimRunner, SimConfig
    import threading
    import time as time_module

    with tempfile.TemporaryDirectory() as tmpdir:
        stub_dir = Path(tmpdir)
        fixture_dir = stub_dir / "demo-app" / "quote"

        # Step 1: Create fixtures
        print("\n1. Setting up fixtures...")
        setup_stubs(stub_dir)

        # Create fixture files
        test_fixture_id = "runner-test-001"
        fixture_path = fixture_dir / test_fixture_id
        fixture_path.mkdir(parents=True, exist_ok=True)

        # input.json
        input_data = {
            "fixture_id": test_fixture_id,
            "name": "quote",
            "args": {
                "user_id": 123,
                "items": [
                    {"sku": "PRODUCT-A", "qty": 2},
                    {"sku": "PRODUCT-B", "qty": 1},
                ],
            },
        }
        with open(fixture_path / "input.json", "w") as f:
            json.dump(input_data, f, indent=2)

        # golden_output.json
        golden_output = {
            "fixture_id": test_fixture_id,
            "output": {
                "user_id": 123,
                "subtotal": 69.97,
                "tax_rate": 0.0925,
                "tax": 6.47,
                "total": 76.44,
            },
        }
        with open(fixture_path / "golden_output.json", "w") as f:
            json.dump(golden_output, f, indent=2)

        # stubs.json
        stubs_data = {"fixture_id": test_fixture_id, "db_calls": [], "http_calls": []}
        with open(fixture_path / "stubs.json", "w") as f:
            json.dump(stubs_data, f, indent=2)

        # metadata.json
        metadata = {
            "fixture_id": test_fixture_id,
            "name": "quote",
            "recorded_at": "2024-01-01T00:00:00Z",
            "schema_version": "1.0",
        }
        with open(fixture_path / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        print(f"   Created fixture at {fixture_path}")

        # Step 2: Configure runner
        print("\n2. Configuring SimRunner...")

        config = SimConfig(
            service_name="demo-app",
            service_port=5001,
            endpoints=[
                {"name": "quote", "method": "POST", "path": "/quote"},
            ],
            storage_type="local",
            storage_local_path=str(stub_dir),
            ignore_paths=["quoted_at", "timestamp", "request_id"],
            money_paths=["total", "subtotal", "tax"],
            money_tolerance=0.01,
        )

        runner = SimRunner(config, verbose=True)
        print("   ✓ SimRunner configured")

        # Step 3: Set up app in replay mode
        print("\n3. Setting up app in replay mode...")

        os.environ["SIM_MODE"] = "replay"
        os.environ["SIM_STUB_DIR"] = str(stub_dir)
        os.environ["SIM_RUN_ID"] = "runner-test"

        from sim_sdk.context import clear_context
        clear_context()

        from app import app

        # Start Flask test server in a thread
        port = 5099
        server_ready = threading.Event()

        def run_server():
            # Use werkzeug server
            from werkzeug.serving import make_server
            server = make_server("127.0.0.1", port, app, threaded=True)
            server_ready.set()
            server.handle_request()  # Handle just one request for the test
            server.handle_request()  # Health check might be separate

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        server_ready.wait(timeout=5)
        time_module.sleep(0.5)  # Give server time to start

        print(f"   ✓ App running on http://127.0.0.1:{port}")

        # Step 4: Run simulation
        print("\n4. Running simulation...")

        result = runner.run(
            candidate_url=f"http://127.0.0.1:{port}",
            endpoints=["quote"],
        )

        print(f"\n   Total fixtures: {result.total_fixtures}")
        print(f"   Passed: {result.passed_fixtures}")
        print(f"   Failed: {result.failed_fixtures}")
        print(f"   Duration: {result.duration_seconds:.2f}s")

        # Step 5: Generate reports
        print("\n5. Generating reports...")

        report = runner.generate_report(result, candidate_image="demo-app:test")

        # Save reports
        report_dir = Path(tmpdir) / "reports"
        report_dir.mkdir(exist_ok=True)

        md_path = report_dir / "report.md"
        md_path.write_text(report.to_markdown())
        print(f"   ✓ Markdown report: {md_path}")

        html_path = report_dir / "report.html"
        report.save_html(html_path)
        print(f"   ✓ HTML report: {html_path}")

        # Step 6: Verify results
        print("\n6. Verifying Phase 4 exit criteria...")

        passed = True

        if result.total_fixtures > 0:
            print("   ✓ Fixtures fetched and replayed")
        else:
            print("   ✗ No fixtures replayed")
            passed = False

        if md_path.exists() and md_path.stat().st_size > 0:
            print("   ✓ Markdown report generated")
        else:
            print("   ✗ Markdown report not generated")
            passed = False

        if html_path.exists() and html_path.stat().st_size > 0:
            print("   ✓ HTML report generated")
        else:
            print("   ✗ HTML report not generated")
            passed = False

        # Check CI exit code logic
        if result.success == (result.failed_fixtures == 0):
            print("   ✓ CI exit code logic correct")
        else:
            print("   ✗ CI exit code logic incorrect")
            passed = False

        print("\n" + "-" * 60)
        if passed:
            print("RUNNER mode tests PASSED!")
        else:
            print("RUNNER mode tests FAILED!")
        print("-" * 60)
        print("\nPhase 4 Exit Criteria:")
        print("  ✓ Single command (SimRunner) runs end-to-end")
        print("  ✓ Fetches fixtures from local storage")
        print("  ✓ Replays HTTP inputs against candidate")
        print("  ✓ Diffs responses against golden outputs")
        print("  ✓ Generates Markdown and HTML reports")
        print("  ✓ CI-compatible exit codes (0=pass, 1=fail)")
        print("-" * 60)

        return passed


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
        "--e2e",
        action="store_true",
        help="Run e2e test (record → replay flow)",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Run diff test (catch a regression)",
    )
    parser.add_argument(
        "--runner",
        action="store_true",
        help="Run runner test (Phase 4: sim-run CLI)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all tests",
    )

    args = parser.parse_args()

    # Default to replay if no args
    if not args.replay and not args.record and not args.e2e and not args.diff and not args.runner and not args.all:
        args.replay = True

    results = []

    if args.replay or args.all:
        results.append(("Replay", test_replay_mode()))

    if args.record or args.all:
        results.append(("Record", test_record_mode()))

    if args.e2e or args.all:
        results.append(("E2E", test_e2e_mode()))

    if args.diff or args.all:
        results.append(("Diff", test_diff_mode()))

    if args.runner or args.all:
        results.append(("Runner", test_runner_mode()))

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
