#!/usr/bin/env python3
"""
Recording script - Generate fixtures by hitting target endpoints.

This script runs in RECORD mode to capture:
- HTTP request/response
- DB query results
- HTTP outbound call responses

Usage:
    # Record from a running service
    python scripts/record.py --config sim.yaml --base-url http://localhost:5000

    # Record specific endpoints
    python scripts/record.py --config sim.yaml --endpoints quote,health

    # Record with explicit fixtures
    python scripts/record.py --fixtures fixtures/quote/*.json --base-url http://localhost:5000

    # Upload to S3
    python scripts/record.py --config sim.yaml --upload-s3 --bucket sim-fixtures
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

# Add sim_sdk to path
sys.path.insert(0, str(Path(__file__).parent.parent / "sim_sdk"))

from sim_sdk.sink import init_sink, get_default_sink


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load sim.yaml configuration."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def load_fixture_file(path: Path) -> Dict[str, Any]:
    """Load a fixture input file."""
    with open(path, "r") as f:
        return json.load(f)


def send_request(
    base_url: str,
    method: str,
    path: str,
    body: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    timeout: int = 30,
) -> requests.Response:
    """
    Send an HTTP request to the target service.
    """
    url = f"{base_url.rstrip('/')}{path}"
    default_headers = {"Content-Type": "application/json"}

    if headers:
        default_headers.update(headers)

    logger.info(f"Sending {method} {url}")

    response = requests.request(
        method=method,
        url=url,
        json=body,
        headers=default_headers,
        timeout=timeout,
    )

    logger.info(f"Response: {response.status_code}")
    return response


def record_endpoint(
    base_url: str,
    endpoint_config: Dict[str, Any],
    fixtures_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Record fixtures for a single endpoint.

    Args:
        base_url: Base URL of the service
        endpoint_config: Endpoint configuration from sim.yaml
        fixtures_dir: Directory containing fixture input files

    Returns:
        List of recorded results
    """
    method = endpoint_config.get("method", "GET")
    path = endpoint_config["path"]
    name = endpoint_config.get("name", path.replace("/", "_"))

    results = []

    # Load fixture inputs if provided
    fixtures = []
    if fixtures_dir and fixtures_dir.exists():
        for fixture_path in sorted(fixtures_dir.glob("*.json")):
            fixtures.append(load_fixture_file(fixture_path))

    # If no fixtures, create a default one
    if not fixtures:
        if method in ["POST", "PUT", "PATCH"]:
            logger.warning(f"No fixtures found for {name}, using empty body")
            fixtures = [{"body": {}}]
        else:
            fixtures = [{}]

    # Record each fixture
    for i, fixture in enumerate(fixtures):
        fixture_name = fixture.get("name", f"{name}_{i:03d}")
        body = fixture.get("body")
        headers = fixture.get("headers", {})

        try:
            response = send_request(
                base_url=base_url,
                method=method,
                path=path,
                body=body,
                headers=headers,
            )

            results.append({
                "fixture_name": fixture_name,
                "endpoint": name,
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "success": response.ok,
            })

        except Exception as e:
            logger.error(f"Failed to record {fixture_name}: {e}")
            results.append({
                "fixture_name": fixture_name,
                "endpoint": name,
                "error": str(e),
                "success": False,
            })

        # Small delay between requests
        time.sleep(0.1)

    return results


def record_from_config(
    config_path: Path,
    base_url: str,
    output_dir: Path,
    endpoints: Optional[List[str]] = None,
    service_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record fixtures for all endpoints in a config file.

    Args:
        config_path: Path to sim.yaml
        base_url: Base URL of the service
        output_dir: Directory to write fixtures
        endpoints: Optional list of endpoint names to record (None = all)
        service_name: Override service name from config

    Returns:
        Summary of recording results
    """
    config = load_config(config_path)

    service = service_name or config.get("service", {}).get("name", "default")
    endpoint_configs = config.get("simulation", {}).get("endpoints", [])

    # Filter endpoints if specified
    if endpoints:
        endpoint_configs = [
            e for e in endpoint_configs
            if e.get("name") in endpoints
        ]

    logger.info(f"Recording {len(endpoint_configs)} endpoints for service '{service}'")

    all_results = []

    for endpoint_config in endpoint_configs:
        endpoint_name = endpoint_config.get("name", "unknown")

        # Initialize sink for this endpoint
        init_sink(
            output_dir=output_dir,
            service_name=service,
            endpoint_name=endpoint_name,
        )

        # Get fixtures directory
        fixtures_dir_str = endpoint_config.get("fixtures_dir")
        fixtures_dir = Path(fixtures_dir_str) if fixtures_dir_str else None

        # Record
        results = record_endpoint(
            base_url=base_url,
            endpoint_config=endpoint_config,
            fixtures_dir=fixtures_dir,
        )

        all_results.extend(results)

        # Flush after each endpoint
        sink = get_default_sink()
        if sink:
            sink.flush()

    # Summary
    successful = sum(1 for r in all_results if r.get("success"))
    failed = len(all_results) - successful

    return {
        "service": service,
        "total_fixtures": len(all_results),
        "successful": successful,
        "failed": failed,
        "results": all_results,
    }


def upload_to_s3(
    local_dir: Path,
    bucket: str,
    region: str = "us-east-1",
) -> None:
    """
    Upload recorded fixtures to S3.
    """
    try:
        import boto3
    except ImportError:
        logger.error("boto3 not installed. Run: pip install boto3")
        return

    s3 = boto3.client("s3", region_name=region)

    # Walk local directory and upload
    for root, dirs, files in os.walk(local_dir):
        for filename in files:
            if not filename.endswith(".json"):
                continue

            local_path = Path(root) / filename
            relative_path = local_path.relative_to(local_dir)
            s3_key = str(relative_path)

            logger.info(f"Uploading {s3_key} to s3://{bucket}/{s3_key}")

            with open(local_path, "rb") as f:
                s3.put_object(
                    Bucket=bucket,
                    Key=s3_key,
                    Body=f,
                    ContentType="application/json",
                )

    logger.info(f"Upload complete to s3://{bucket}/")


def main():
    parser = argparse.ArgumentParser(
        description="Record fixtures from a running service",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to sim.yaml configuration file",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:5000",
        help="Base URL of the service to record from",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./recorded-fixtures"),
        help="Directory to write recorded fixtures",
    )
    parser.add_argument(
        "--endpoints",
        type=str,
        help="Comma-separated list of endpoint names to record",
    )
    parser.add_argument(
        "--service-name",
        type=str,
        help="Override service name from config",
    )
    parser.add_argument(
        "--upload-s3",
        action="store_true",
        help="Upload fixtures to S3 after recording",
    )
    parser.add_argument(
        "--bucket",
        type=str,
        help="S3 bucket for upload",
    )
    parser.add_argument(
        "--region",
        type=str,
        default="us-east-1",
        help="AWS region for S3",
    )
    parser.add_argument(
        "--fixtures",
        type=str,
        help="Glob pattern for fixture input files",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Set environment for record mode
    os.environ["SIM_MODE"] = "record"
    os.environ["SIM_STUB_DIR"] = str(args.output_dir)

    # Record
    if args.config:
        endpoints = args.endpoints.split(",") if args.endpoints else None

        summary = record_from_config(
            config_path=args.config,
            base_url=args.base_url,
            output_dir=args.output_dir,
            endpoints=endpoints,
            service_name=args.service_name,
        )

        print("\n" + "=" * 60)
        print("RECORDING SUMMARY")
        print("=" * 60)
        print(f"Service: {summary['service']}")
        print(f"Total fixtures: {summary['total_fixtures']}")
        print(f"Successful: {summary['successful']}")
        print(f"Failed: {summary['failed']}")
        print(f"Output directory: {args.output_dir}")

        if summary['failed'] > 0:
            print("\nFailed fixtures:")
            for r in summary['results']:
                if not r.get('success'):
                    print(f"  - {r.get('fixture_name')}: {r.get('error', 'unknown error')}")

    else:
        print("No config file specified. Use --config to specify sim.yaml")
        sys.exit(1)

    # Upload to S3 if requested
    if args.upload_s3:
        if not args.bucket:
            print("Error: --bucket required for S3 upload")
            sys.exit(1)

        print(f"\nUploading to S3 bucket: {args.bucket}")
        upload_to_s3(args.output_dir, args.bucket, args.region)

    print("\nDone!")


if __name__ == "__main__":
    main()
