"""
Fixture fetcher - Download fixtures from S3 for replay.

Downloads fixture sets from S3 to local cache for use in replay mode.
Supports both S3 and local filesystem sources.
"""

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Fixture:
    """
    A complete fixture with input, output, stubs, and metadata.
    """
    fixture_id: str
    name: str
    input: Dict[str, Any]
    golden_output: Dict[str, Any]
    stubs: Dict[str, Any]
    metadata: Dict[str, Any]

    @property
    def db_stubs(self) -> List[Dict[str, Any]]:
        """Get DB call stubs."""
        return self.stubs.get("db_calls", [])

    @property
    def http_stubs(self) -> List[Dict[str, Any]]:
        """Get HTTP call stubs."""
        return self.stubs.get("http_calls", [])


class FixtureFetcher:
    """
    Fetches fixtures from S3 or local filesystem.

    Usage:
        fetcher = FixtureFetcher(
            bucket="sim-fixtures",
            local_cache="/tmp/sim-cache"
        )
        fixtures = fetcher.fetch_endpoint("pricing-api", "quote")
    """

    def __init__(
        self,
        bucket: Optional[str] = None,
        region: str = "us-east-1",
        local_cache: Optional[Path] = None,
        local_source: Optional[Path] = None,
    ):
        """
        Initialize the fixture fetcher.

        Args:
            bucket: S3 bucket name (optional, for S3 fetching)
            region: AWS region
            local_cache: Local directory to cache fetched fixtures
            local_source: Local directory to fetch from (alternative to S3)
        """
        self.bucket = bucket
        self.region = region
        self.local_cache = local_cache or Path("/tmp/sim-cache")
        self.local_source = local_source
        self._s3_client = None

        # Ensure cache directory exists
        self.local_cache.mkdir(parents=True, exist_ok=True)

    def _get_s3_client(self):
        """Lazy initialization of S3 client."""
        if self._s3_client is None:
            try:
                import boto3
                self._s3_client = boto3.client("s3", region_name=self.region)
            except ImportError:
                logger.error("boto3 not installed. Run: pip install boto3")
                raise
        return self._s3_client

    def fetch_endpoint(
        self,
        service: str,
        endpoint: str,
        force_refresh: bool = False,
    ) -> List[Fixture]:
        """
        Fetch all fixtures for a service endpoint.

        Args:
            service: Service name
            endpoint: Endpoint name
            force_refresh: If True, re-fetch even if cached

        Returns:
            List of Fixture objects
        """
        cache_dir = self.local_cache / service / endpoint

        # Check if we need to fetch
        if not force_refresh and cache_dir.exists() and any(cache_dir.iterdir()):
            logger.info(f"Using cached fixtures from {cache_dir}")
        else:
            # Fetch from source
            if self.local_source:
                self._fetch_from_local(service, endpoint, cache_dir)
            elif self.bucket:
                self._fetch_from_s3(service, endpoint, cache_dir)
            else:
                raise ValueError("No fixture source configured (bucket or local_source)")

        # Load fixtures from cache
        return self._load_fixtures(cache_dir)

    def fetch_fixture(
        self,
        service: str,
        endpoint: str,
        fixture_id: str,
        force_refresh: bool = False,
    ) -> Optional[Fixture]:
        """
        Fetch a single fixture by ID.

        Args:
            service: Service name
            endpoint: Endpoint name
            fixture_id: Fixture ID
            force_refresh: If True, re-fetch even if cached

        Returns:
            Fixture object or None if not found
        """
        cache_dir = self.local_cache / service / endpoint / fixture_id

        if not force_refresh and cache_dir.exists():
            logger.debug(f"Using cached fixture {fixture_id}")
        else:
            if self.local_source:
                source_dir = self.local_source / service / endpoint / fixture_id
                if source_dir.exists():
                    shutil.copytree(source_dir, cache_dir, dirs_exist_ok=True)
            elif self.bucket:
                self._fetch_fixture_from_s3(service, endpoint, fixture_id, cache_dir)
            else:
                return None

        return self._load_fixture(cache_dir)

    def _fetch_from_local(
        self,
        service: str,
        endpoint: str,
        cache_dir: Path,
    ) -> None:
        """
        Fetch fixtures from local filesystem.
        """
        source_dir = self.local_source / service / endpoint

        if not source_dir.exists():
            logger.warning(f"Source directory not found: {source_dir}")
            return

        # Copy to cache
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        shutil.copytree(source_dir, cache_dir)

        logger.info(f"Copied fixtures from {source_dir} to {cache_dir}")

    def _fetch_from_s3(
        self,
        service: str,
        endpoint: str,
        cache_dir: Path,
    ) -> None:
        """
        Fetch fixtures from S3.
        """
        s3 = self._get_s3_client()
        prefix = f"{service}/{endpoint}/"

        # List all objects under the prefix
        paginator = s3.get_paginator("list_objects_v2")

        fixture_keys: Dict[str, List[str]] = {}  # fixture_id -> list of keys

        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Extract fixture_id from path: service/endpoint/fixture_id/file.json
                parts = key[len(prefix) :].split("/")
                if len(parts) >= 2:
                    fixture_id = parts[0]
                    if fixture_id not in fixture_keys:
                        fixture_keys[fixture_id] = []
                    fixture_keys[fixture_id].append(key)

        logger.info(f"Found {len(fixture_keys)} fixtures in s3://{self.bucket}/{prefix}")

        # Download each fixture
        for fixture_id, keys in fixture_keys.items():
            fixture_dir = cache_dir / fixture_id
            fixture_dir.mkdir(parents=True, exist_ok=True)

            for key in keys:
                filename = key.split("/")[-1]
                local_path = fixture_dir / filename

                s3.download_file(self.bucket, key, str(local_path))
                logger.debug(f"Downloaded {key} to {local_path}")

    def _fetch_fixture_from_s3(
        self,
        service: str,
        endpoint: str,
        fixture_id: str,
        cache_dir: Path,
    ) -> None:
        """
        Fetch a single fixture from S3.
        """
        s3 = self._get_s3_client()
        prefix = f"{service}/{endpoint}/{fixture_id}/"

        cache_dir.mkdir(parents=True, exist_ok=True)

        # List and download files for this fixture
        response = s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)

        for obj in response.get("Contents", []):
            key = obj["Key"]
            filename = key.split("/")[-1]
            local_path = cache_dir / filename

            s3.download_file(self.bucket, key, str(local_path))

    def _load_fixtures(self, cache_dir: Path) -> List[Fixture]:
        """
        Load all fixtures from a cache directory.
        """
        fixtures = []

        if not cache_dir.exists():
            return fixtures

        for fixture_dir in cache_dir.iterdir():
            if fixture_dir.is_dir():
                fixture = self._load_fixture(fixture_dir)
                if fixture:
                    fixtures.append(fixture)

        logger.info(f"Loaded {len(fixtures)} fixtures from {cache_dir}")
        return fixtures

    def _load_fixture(self, fixture_dir: Path) -> Optional[Fixture]:
        """
        Load a single fixture from a directory.
        """
        try:
            # Load required files
            input_data = self._load_json(fixture_dir / "input.json")
            golden_output = self._load_json(fixture_dir / "golden_output.json")
            stubs = self._load_json(fixture_dir / "stubs.json")
            metadata = self._load_json(fixture_dir / "metadata.json")

            if not all([input_data, golden_output, stubs, metadata]):
                logger.warning(f"Incomplete fixture at {fixture_dir}")
                return None

            return Fixture(
                fixture_id=metadata.get("fixture_id", fixture_dir.name),
                name=metadata.get("name", ""),
                input=input_data,
                golden_output=golden_output,
                stubs=stubs,
                metadata=metadata,
            )

        except Exception as e:
            logger.error(f"Failed to load fixture from {fixture_dir}: {e}")
            return None

    def _load_json(self, path: Path) -> Optional[Dict[str, Any]]:
        """
        Load a JSON file.
        """
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load {path}: {e}")
            return None

    def clear_cache(self, service: Optional[str] = None, endpoint: Optional[str] = None) -> None:
        """
        Clear the local cache.

        Args:
            service: If provided, only clear this service
            endpoint: If provided (with service), only clear this endpoint
        """
        if service and endpoint:
            cache_dir = self.local_cache / service / endpoint
        elif service:
            cache_dir = self.local_cache / service
        else:
            cache_dir = self.local_cache

        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            logger.info(f"Cleared cache at {cache_dir}")


def load_fixtures_for_replay(
    stub_dir: Path,
    service: str,
    endpoint: str,
) -> Dict[str, Fixture]:
    """
    Load fixtures for replay mode.

    Returns a dict mapping fixture_id to Fixture for easy lookup.

    Args:
        stub_dir: Local directory containing fixtures
        service: Service name
        endpoint: Endpoint name

    Returns:
        Dict mapping fixture_id to Fixture
    """
    fetcher = FixtureFetcher(local_source=stub_dir, local_cache=stub_dir)
    fixtures = fetcher.fetch_endpoint(service, endpoint)
    return {f.fixture_id: f for f in fixtures}
