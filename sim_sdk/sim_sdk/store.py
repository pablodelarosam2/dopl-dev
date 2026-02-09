"""
Stub store for reading and writing recorded data.

Provides filesystem-based storage for:
- HTTP request/response pairs
- Database query results
- Request captures
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class StubStore:
    """
    Filesystem-based storage for simulation stubs.

    Directory structure:
        base_dir/
            http/
                <fingerprint>.json
            db/
                <fingerprint>_<ordinal>.json
            requests/
                <request_id>.json
    """

    def __init__(self, base_dir: Union[str, Path]):
        """
        Initialize the stub store.

        Args:
            base_dir: Base directory for stub files
        """
        self.base_dir = Path(base_dir)
        self._http_dir = self.base_dir / "http"
        self._db_dir = self.base_dir / "db"
        self._requests_dir = self.base_dir / "requests"

    def _ensure_dir(self, path: Path) -> None:
        """Ensure a directory exists."""
        path.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # HTTP Stubs
    # =========================================================================

    def save_http(
        self,
        fingerprint: str,
        response: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """
        Save an HTTP response stub.

        Args:
            fingerprint: Request fingerprint
            response: Response data (status_code, headers, body)
            metadata: Optional metadata about the request

        Returns:
            Path to saved file
        """
        self._ensure_dir(self._http_dir)

        data = {
            "fingerprint": fingerprint,
            "response": response,
        }
        if metadata:
            data["metadata"] = metadata

        file_path = self._http_dir / f"{fingerprint}.json"
        self._write_json(file_path, data)
        return file_path

    def load_http(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        """
        Load an HTTP response stub.

        Args:
            fingerprint: Request fingerprint

        Returns:
            Response data or None if not found
        """
        file_path = self._http_dir / f"{fingerprint}.json"
        data = self._read_json(file_path)
        return data.get("response") if data else None

    def has_http(self, fingerprint: str) -> bool:
        """Check if an HTTP stub exists."""
        file_path = self._http_dir / f"{fingerprint}.json"
        return file_path.exists()

    # =========================================================================
    # Database Stubs
    # =========================================================================

    def save_db(
        self,
        fingerprint: str,
        ordinal: int,
        rows: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """
        Save a database query result stub.

        Args:
            fingerprint: Query fingerprint (hash of SQL + params)
            ordinal: Call ordinal (for multiple calls with same fingerprint)
            rows: Query result rows
            metadata: Optional metadata (SQL, params, etc.)

        Returns:
            Path to saved file
        """
        self._ensure_dir(self._db_dir)

        data = {
            "fingerprint": fingerprint,
            "ordinal": ordinal,
            "rows": rows,
        }
        if metadata:
            data["metadata"] = metadata

        file_path = self._db_dir / f"{fingerprint}_{ordinal}.json"
        self._write_json(file_path, data)
        return file_path

    def load_db(
        self,
        fingerprint: str,
        ordinal: int,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Load a database query result stub.

        Args:
            fingerprint: Query fingerprint
            ordinal: Call ordinal

        Returns:
            List of row dicts or None if not found
        """
        file_path = self._db_dir / f"{fingerprint}_{ordinal}.json"
        data = self._read_json(file_path)
        return data.get("rows") if data else None

    def has_db(self, fingerprint: str, ordinal: int) -> bool:
        """Check if a database stub exists."""
        file_path = self._db_dir / f"{fingerprint}_{ordinal}.json"
        return file_path.exists()

    # =========================================================================
    # Request Captures
    # =========================================================================

    def save_request(
        self,
        request_id: str,
        data: Dict[str, Any],
    ) -> Path:
        """
        Save a captured request/response pair.

        Args:
            request_id: Unique request identifier
            data: Request and response data

        Returns:
            Path to saved file
        """
        self._ensure_dir(self._requests_dir)

        file_path = self._requests_dir / f"{request_id}.json"
        self._write_json(file_path, data)
        return file_path

    def load_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Load a captured request/response pair.

        Args:
            request_id: Unique request identifier

        Returns:
            Request data or None if not found
        """
        file_path = self._requests_dir / f"{request_id}.json"
        return self._read_json(file_path)

    def list_requests(self) -> List[str]:
        """
        List all captured request IDs.

        Returns:
            List of request IDs
        """
        if not self._requests_dir.exists():
            return []

        return [
            f.stem
            for f in self._requests_dir.glob("*.json")
        ]

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def save_fixture(
        self,
        name: str,
        request: Dict[str, Any],
        db_stubs: Optional[List[Dict[str, Any]]] = None,
        http_stubs: Optional[List[Dict[str, Any]]] = None,
    ) -> Path:
        """
        Save a complete fixture (request + all stubs).

        Args:
            name: Fixture name
            request: HTTP request data
            db_stubs: List of DB stubs with fingerprint, ordinal, rows
            http_stubs: List of HTTP stubs with fingerprint, response

        Returns:
            Path to fixture file
        """
        fixtures_dir = self.base_dir / "fixtures"
        self._ensure_dir(fixtures_dir)

        data = {
            "name": name,
            "request": request,
        }

        if db_stubs:
            data["db_stubs"] = db_stubs
        if http_stubs:
            data["http_stubs"] = http_stubs

        file_path = fixtures_dir / f"{name}.json"
        self._write_json(file_path, data)
        return file_path

    def load_fixture(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Load a complete fixture.

        Args:
            name: Fixture name

        Returns:
            Fixture data or None if not found
        """
        file_path = self.base_dir / "fixtures" / f"{name}.json"
        return self._read_json(file_path)

    def list_fixtures(self) -> List[str]:
        """List all fixture names."""
        fixtures_dir = self.base_dir / "fixtures"
        if not fixtures_dir.exists():
            return []

        return [f.stem for f in fixtures_dir.glob("*.json")]

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _write_json(self, path: Path, data: Any) -> None:
        """Write data to a JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read data from a JSON file."""
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def clear(self) -> None:
        """Clear all stubs (use with caution)."""
        import shutil

        for subdir in [self._http_dir, self._db_dir, self._requests_dir]:
            if subdir.exists():
                shutil.rmtree(subdir)

    def stats(self) -> Dict[str, int]:
        """Get statistics about stored stubs."""
        def count_files(dir_path: Path) -> int:
            if not dir_path.exists():
                return 0
            return len(list(dir_path.glob("*.json")))

        return {
            "http_stubs": count_files(self._http_dir),
            "db_stubs": count_files(self._db_dir),
            "requests": count_files(self._requests_dir),
            "fixtures": count_files(self.base_dir / "fixtures"),
        }
