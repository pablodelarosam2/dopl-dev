"""
sim_runner - CLI orchestrator for PR-level simulation validation.

This tool orchestrates the simulation end-to-end:
1. Fetches fixture set from S3/local for affected endpoints
2. Starts candidate container with SIM_MODE=replay
3. Replays fixture HTTP inputs against the candidate
4. Collects candidate responses
5. Runs diff engine (candidate output vs golden output)
6. Generates markdown/HTML report
7. Exits with CI-compatible status code (0=pass, 1=fail)

Usage:
    sim-run --config sim.yaml --candidate myapp:pr-123
    sim-run --config sim.yaml --local-app http://localhost:5000

CLI Interface:
    sim-run [OPTIONS]

Options:
    --config PATH       Path to sim.yaml configuration file
    --candidate IMAGE   Docker image to test (pulls/builds if needed)
    --local-app URL     URL of already-running local app (for development)
    --fixtures PATH     Override fixtures directory
    --output PATH       Output directory for reports
    --html              Generate HTML report
    --json              Generate JSON report
    --verbose           Verbose output
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
import yaml

from sim_sdk.diff import DiffEngine, DiffConfig, DiffResult, SimulationReport
from sim_sdk.fetch import FixtureFetcher, Fixture
from sim_sdk.http_patch import unpatched_request


@dataclass
class SimConfig:
    """Configuration loaded from sim.yaml."""
    service_name: str = "unknown"
    service_port: int = 8080

    # Endpoints to test
    endpoints: List[Dict[str, Any]] = field(default_factory=list)

    # Storage settings
    storage_type: str = "local"  # "local" or "s3"
    storage_bucket: str = ""
    storage_local_path: str = "./fixtures"
    local_cache: str = "/tmp/sim-cache"

    # Diff settings
    ignore_paths: List[str] = field(default_factory=lambda: [
        "request_id", "trace_id", "timestamp", "quoted_at",
    ])
    money_paths: List[str] = field(default_factory=lambda: [
        "total", "subtotal", "tax", "price", "amount",
    ])
    money_tolerance: float = 0.01

    # Recording settings
    buffer_size_kb: int = 512
    flush_interval_ms: int = 200

    @classmethod
    def from_yaml(cls, path: Path) -> "SimConfig":
        """Load configuration from YAML file."""
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        service = data.get("service", {})
        storage = data.get("storage", {})
        simulation = data.get("simulation", {})
        recording = data.get("recording", {})
        ignore = data.get("ignore", {})
        tolerances = data.get("tolerances", {})

        return cls(
            service_name=service.get("name", "unknown"),
            service_port=service.get("port", 8080),
            endpoints=simulation.get("endpoints", []),
            storage_type=storage.get("type", "local"),
            storage_bucket=storage.get("bucket", ""),
            storage_local_path=storage.get("local_path", "./fixtures"),
            local_cache=storage.get("local_cache", "/tmp/sim-cache"),
            ignore_paths=ignore.get("jsonpaths", [
                "request_id", "trace_id", "timestamp", "quoted_at",
            ]),
            money_paths=tolerances.get("money_paths", [
                "total", "subtotal", "tax", "price", "amount",
            ]),
            money_tolerance=tolerances.get("money_abs", 0.01),
            buffer_size_kb=recording.get("buffer_size_kb", 512),
            flush_interval_ms=recording.get("flush_interval_ms", 200),
        )

    def to_diff_config(self) -> DiffConfig:
        """Convert to DiffConfig for the diff engine."""
        return DiffConfig(
            ignore_paths=self.ignore_paths,
            money_paths=self.money_paths,
            money_tolerance=self.money_tolerance,
        )


@dataclass
class RunnerResult:
    """Result of a simulation run."""
    success: bool
    total_fixtures: int
    passed_fixtures: int
    failed_fixtures: int
    diff_results: List[DiffResult] = field(default_factory=list)
    stub_misses: List[Dict[str, Any]] = field(default_factory=list)
    blocked_writes: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0


class SimRunner:
    """
    Orchestrates simulation validation for PR workflows.

    Usage:
        runner = SimRunner(config)
        result = runner.run(candidate_url="http://localhost:5000")

        if not result.success:
            print("Regressions detected!")
            sys.exit(1)
    """

    def __init__(self, config: SimConfig, verbose: bool = False):
        self.config = config
        self.verbose = verbose
        self.diff_engine = DiffEngine(config.to_diff_config())

    def log(self, msg: str) -> None:
        """Print if verbose mode is enabled."""
        if self.verbose:
            print(f"[sim-run] {msg}")

    def fetch_fixtures(self, endpoint_name: str) -> List[Fixture]:
        """Fetch fixtures for an endpoint."""
        self.log(f"Fetching fixtures for {self.config.service_name}/{endpoint_name}")

        if self.config.storage_type == "s3":
            fetcher = FixtureFetcher(
                s3_bucket=self.config.storage_bucket,
                local_cache=Path(self.config.local_cache),
            )
        else:
            fetcher = FixtureFetcher(
                local_source=Path(self.config.storage_local_path),
                local_cache=Path(self.config.local_cache),
            )

        fixtures = fetcher.fetch_endpoint(self.config.service_name, endpoint_name)
        self.log(f"  Found {len(fixtures)} fixtures")
        return fixtures

    def replay_fixture(
        self,
        base_url: str,
        fixture: Fixture,
        endpoint_config: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Replay a single fixture against the candidate.

        Returns:
            Tuple of (response_dict, error_message)
        """
        method = endpoint_config.get("method", "POST").upper()
        path = endpoint_config.get("path", "/")

        # Build request from fixture input
        input_data = fixture.input.get("args", fixture.input)

        # Handle different input formats
        if isinstance(input_data, dict):
            # Check if it's HTTP request format
            if "method" in input_data and "path" in input_data:
                method = input_data.get("method", method)
                path = input_data.get("path", path)
                body = input_data.get("body")
                headers = input_data.get("headers", {})
            else:
                # Assume it's the request body
                body = input_data
                headers = {"Content-Type": "application/json"}
        else:
            body = input_data
            headers = {}

        url = urljoin(base_url, path)

        try:
            # Use unpatched_request to bypass simulation interception
            # This ensures runner's calls to candidate service aren't treated as stubbed calls
            if method == "GET":
                response = unpatched_request("GET", url, params=body, headers=headers, timeout=30)
            elif method == "POST":
                response = unpatched_request("POST", url, json=body, headers=headers, timeout=30)
            elif method == "PUT":
                response = unpatched_request("PUT", url, json=body, headers=headers, timeout=30)
            elif method == "DELETE":
                response = unpatched_request("DELETE", url, headers=headers, timeout=30)
            else:
                return {}, f"Unsupported HTTP method: {method}"

            # Build response dict
            result = {
                "status": response.status_code,
                "status_code": response.status_code,
            }

            try:
                result["body"] = response.json()
            except (json.JSONDecodeError, ValueError):
                result["body"] = response.text

            return result, None

        except requests.exceptions.RequestException as e:
            return {}, f"Request failed: {str(e)}"

    def run(
        self,
        candidate_url: str,
        endpoints: Optional[List[str]] = None,
    ) -> RunnerResult:
        """
        Run simulation against a candidate service.

        Args:
            candidate_url: Base URL of the candidate service
            endpoints: Specific endpoints to test (None = all configured)

        Returns:
            RunnerResult with pass/fail status and details
        """
        start_time = time.time()
        self.log(f"Starting simulation run against {candidate_url}")

        diff_results: List[DiffResult] = []
        stub_misses: List[Dict[str, Any]] = []
        errors: List[str] = []

        total_fixtures = 0
        passed_fixtures = 0
        failed_fixtures = 0

        # Get endpoints to test
        endpoint_configs = self.config.endpoints
        if endpoints:
            endpoint_configs = [e for e in endpoint_configs if e.get("name") in endpoints]

        if not endpoint_configs:
            errors.append("No endpoints configured for testing")
            return RunnerResult(
                success=False,
                total_fixtures=0,
                passed_fixtures=0,
                failed_fixtures=0,
                errors=errors,
            )

        # Test each endpoint
        for endpoint_config in endpoint_configs:
            endpoint_name = endpoint_config.get("name", "unknown")
            self.log(f"\nTesting endpoint: {endpoint_name}")

            # Fetch fixtures
            try:
                fixtures = self.fetch_fixtures(endpoint_name)
            except Exception as e:
                errors.append(f"Failed to fetch fixtures for {endpoint_name}: {str(e)}")
                continue

            if not fixtures:
                self.log(f"  No fixtures found for {endpoint_name}")
                continue

            # Replay each fixture
            for fixture in fixtures:
                total_fixtures += 1
                self.log(f"  Replaying fixture: {fixture.fixture_id}")

                # Get candidate response
                candidate_response, error = self.replay_fixture(
                    candidate_url, fixture, endpoint_config
                )

                if error:
                    errors.append(f"Fixture {fixture.fixture_id}: {error}")
                    failed_fixtures += 1
                    continue

                # Get golden output
                golden_output = fixture.golden_output.get("output", fixture.golden_output)
                golden = {"status": 200, "body": golden_output}

                # Compare
                diff_result = self.diff_engine.compare(
                    fixture_id=fixture.fixture_id,
                    endpoint=endpoint_config.get("path", f"/{endpoint_name}"),
                    golden=golden,
                    candidate=candidate_response,
                )

                diff_results.append(diff_result)

                if diff_result.passed:
                    passed_fixtures += 1
                    self.log(f"    ✓ PASSED")
                else:
                    failed_fixtures += 1
                    self.log(f"    ✗ FAILED ({len(diff_result.differences)} differences)")

        duration = time.time() - start_time
        success = failed_fixtures == 0 and len(errors) == 0 and len(stub_misses) == 0

        self.log(f"\nSimulation complete in {duration:.2f}s")
        self.log(f"  Total: {total_fixtures}, Passed: {passed_fixtures}, Failed: {failed_fixtures}")

        return RunnerResult(
            success=success,
            total_fixtures=total_fixtures,
            passed_fixtures=passed_fixtures,
            failed_fixtures=failed_fixtures,
            diff_results=diff_results,
            stub_misses=stub_misses,
            errors=errors,
            duration_seconds=duration,
        )

    def generate_report(self, result: RunnerResult, candidate_image: str = "unknown") -> SimulationReport:
        """Generate a SimulationReport from runner result."""
        return SimulationReport(
            run_id=f"sim-{int(time.time())}",
            candidate_image=candidate_image,
            total_fixtures=result.total_fixtures,
            passed_fixtures=result.passed_fixtures,
            failed_fixtures=result.failed_fixtures,
            results=result.diff_results,
            stub_misses=result.stub_misses,
            blocked_writes=[],
        )


def create_default_config() -> str:
    """Generate a default sim.yaml configuration."""
    return '''# sim.yaml - Simulation Configuration
service:
  name: my-service
  port: 8080

simulation:
  endpoints:
    - name: quote
      method: POST
      path: /quote
      fixtures_dir: fixtures/quote

storage:
  type: local          # local or s3
  local_path: ./fixtures
  local_cache: /tmp/sim-cache
  # s3 settings (if type: s3)
  # bucket: sim-fixtures

recording:
  buffer_size_kb: 512
  flush_interval_ms: 200
  sink: local          # local, s3, or kafka (future)

ignore:
  jsonpaths:
    - "$.request_id"
    - "$.trace_id"
    - "$.timestamp"
    - "$.quoted_at"

tolerances:
  money_paths:
    - "$.total"
    - "$.subtotal"
    - "$.tax"
  money_abs: 0.01
'''


def main():
    """CLI entry point for sim-run."""
    parser = argparse.ArgumentParser(
        description="Run simulation validation against a candidate service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test against a local running app
  sim-run --config sim.yaml --local-app http://localhost:5000

  # Test against a Docker image (coming soon)
  sim-run --config sim.yaml --candidate myapp:pr-123

  # Generate HTML report
  sim-run --config sim.yaml --local-app http://localhost:5000 --html --output ./reports
        """,
    )

    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=Path("sim.yaml"),
        help="Path to sim.yaml configuration file",
    )
    parser.add_argument(
        "--candidate",
        type=str,
        help="Docker image to test (not yet implemented)",
    )
    parser.add_argument(
        "--local-app",
        type=str,
        help="URL of already-running local app",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        help="Override fixtures directory",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("./sim-reports"),
        help="Output directory for reports",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Generate HTML report",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Generate JSON report",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Generate default sim.yaml config",
    )
    parser.add_argument(
        "--endpoints",
        type=str,
        nargs="+",
        help="Specific endpoints to test",
    )

    args = parser.parse_args()

    # Handle --init
    if args.init:
        config_path = args.config
        if config_path.exists():
            print(f"Error: {config_path} already exists")
            sys.exit(1)

        config_path.write_text(create_default_config())
        print(f"Created {config_path}")
        sys.exit(0)

    # Validate arguments
    if not args.local_app and not args.candidate:
        print("Error: Either --local-app or --candidate is required")
        parser.print_help()
        sys.exit(1)

    if args.candidate:
        print("Error: Docker candidate mode not yet implemented. Use --local-app for now.")
        sys.exit(1)

    # Load config
    try:
        config = SimConfig.from_yaml(args.config)
    except FileNotFoundError:
        print(f"Error: Config file not found: {args.config}")
        print("Run 'sim-run --init' to create a default config")
        sys.exit(1)
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)

    # Override fixtures path if specified
    if args.fixtures:
        config.storage_local_path = str(args.fixtures)
        config.storage_type = "local"

    # Create runner
    runner = SimRunner(config, verbose=args.verbose)

    # Run simulation
    print(f"Running simulation against {args.local_app}")
    print(f"Config: {args.config}")
    print("-" * 60)

    result = runner.run(
        candidate_url=args.local_app,
        endpoints=args.endpoints,
    )

    # Generate report
    report = runner.generate_report(result, candidate_image=args.local_app)

    # Print summary
    print("\n" + "=" * 60)
    print("SIMULATION SUMMARY")
    print("=" * 60)
    print(f"Total Fixtures: {result.total_fixtures}")
    print(f"Passed:         {result.passed_fixtures}")
    print(f"Failed:         {result.failed_fixtures}")
    print(f"Duration:       {result.duration_seconds:.2f}s")
    print("=" * 60)

    if result.errors:
        print("\nErrors:")
        for error in result.errors:
            print(f"  - {error}")

    # Save reports
    args.output.mkdir(parents=True, exist_ok=True)

    # Always save markdown
    md_path = args.output / "report.md"
    md_path.write_text(report.to_markdown())
    print(f"\nMarkdown report: {md_path}")

    if args.html:
        html_path = args.output / "report.html"
        report.save_html(html_path)
        print(f"HTML report: {html_path}")

    if args.json:
        json_path = args.output / "report.json"
        json_path.write_text(json.dumps(report.to_dict(), indent=2))
        print(f"JSON report: {json_path}")

    # Exit with appropriate code
    if result.success:
        print("\n✅ PASSED - No regressions detected")
        sys.exit(0)
    else:
        print("\n❌ FAILED - Regressions detected")
        sys.exit(1)


if __name__ == "__main__":
    main()
