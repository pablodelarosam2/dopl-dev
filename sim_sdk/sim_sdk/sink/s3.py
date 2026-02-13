"""
S3 sink implementation with local buffering.

Writes fixtures to local disk first, then uploads to S3.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from . import RecordSink


class S3Sink(RecordSink):
    """
    Writes fixtures to S3 with local buffering.
    
    Strategy:
    1. Buffer to local disk first (fast)
    2. Upload to S3 in background or on flush/close
    3. Optionally delete local files after successful upload
    """
    
    def __init__(
        self,
        bucket: str,
        prefix: str = "fixtures/",
        local_buffer_dir: str = ".sim/buffer",
        keep_local: bool = False
    ):
        """
        Initialize S3 sink.
        
        Args:
            bucket: S3 bucket name
            prefix: Key prefix for fixtures in S3
            local_buffer_dir: Local directory for buffering
            keep_local: Whether to keep local files after upload
        """
        self.bucket = bucket
        self.prefix = prefix
        self.keep_local = keep_local
        self.local_buffer_dir = Path(local_buffer_dir)
        self.local_buffer_dir.mkdir(parents=True, exist_ok=True)
        
        # Lazy import boto3 (only needed if S3 sink is used)
        self._s3_client = None
    
    @property
    def s3_client(self):
        """Lazy initialize S3 client."""
        if self._s3_client is None:
            try:
                import boto3
                self._s3_client = boto3.client('s3')
            except ImportError:
                raise ImportError(
                    "boto3 is required for S3Sink. Install with: pip install boto3"
                )
        return self._s3_client
    
    def write(self, fixture_id: str, data: Dict[str, Any]) -> None:
        """
        Write fixture to local buffer.
        
        Args:
            fixture_id: Unique identifier for the fixture
            data: Fixture data (JSON-serializable dict)
        """
        filepath = self.local_buffer_dir / f"{fixture_id}.json"
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)
    
    def flush(self) -> None:
        """
        Upload all buffered fixtures to S3.
        """
        for filepath in self.local_buffer_dir.glob("*.json"):
            self._upload_file(filepath)
    
    def close(self) -> None:
        """
        Flush and clean up resources.
        """
        self.flush()
    
    def _upload_file(self, filepath: Path) -> None:
        """
        Upload a single file to S3.
        
        Args:
            filepath: Path to local file
        """
        key = f"{self.prefix}{filepath.name}"
        
        try:
            self.s3_client.upload_file(
                str(filepath),
                self.bucket,
                key
            )
            
            # Delete local file if not keeping
            if not self.keep_local:
                filepath.unlink()
                
        except Exception as e:
            # Log error but don't fail - keep local file
            print(f"Warning: Failed to upload {filepath.name} to S3: {e}")
