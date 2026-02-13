"""
Local filesystem sink implementation.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any
from . import RecordSink


class LocalSink(RecordSink):
    """
    Writes fixtures to local disk.
    
    Files are organized as:
        {output_dir}/{fixture_id}.json
    """
    
    def __init__(self, output_dir: str = ".sim/fixtures"):
        """
        Initialize local sink.
        
        Args:
            output_dir: Directory to write fixtures to
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def write(self, fixture_id: str, data: Dict[str, Any]) -> None:
        """
        Write a fixture to disk.
        
        Args:
            fixture_id: Unique identifier for the fixture
            data: Fixture data (JSON-serializable dict)
        """
        filepath = self.output_dir / f"{fixture_id}.json"
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)
    
    def flush(self) -> None:
        """No buffering in local sink, so flush is a no-op."""
        pass
    
    def close(self) -> None:
        """No resources to release for local sink."""
        pass
