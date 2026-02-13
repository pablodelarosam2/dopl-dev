"""
Fixture writer - writes fixture files to sinks.
"""

from typing import Optional
from ..sink import RecordSink
from ..canonical import fingerprint_short
from .schema import Fixture
from datetime import datetime


class FixtureWriter:
    """
    Writes fixture data to a sink.
    
    Handles:
    - Generating fixture IDs
    - Serializing fixture data
    - Writing to the configured sink
    """
    
    def __init__(self, sink: RecordSink):
        """
        Initialize fixture writer.
        
        Args:
            sink: Recording sink to write fixtures to
        """
        self.sink = sink
    
    def write_fixture(self, fixture: Fixture) -> str:
        """
        Write a fixture to the sink.
        
        Args:
            fixture: Fixture to write
            
        Returns:
            Fixture ID
        """
        # Generate fixture ID if not set
        if not fixture.fixture_id:
            fixture.fixture_id = self._generate_fixture_id(fixture)
        
        # Convert to dict and write
        fixture_data = fixture.to_dict()
        self.sink.write(fixture.fixture_id, fixture_data)
        
        return fixture.fixture_id
    
    def flush(self) -> None:
        """Flush the underlying sink."""
        self.sink.flush()
    
    def close(self) -> None:
        """Close the writer and underlying sink."""
        self.sink.close()
    
    def _generate_fixture_id(self, fixture: Fixture) -> str:
        """
        Generate a unique fixture ID.
        
        Strategy: Use timestamp + short content hash
        
        Args:
            fixture: Fixture to generate ID for
            
        Returns:
            Generated fixture ID
        """
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        content_hash = fingerprint_short(fixture.to_dict(), length=8)
        return f"{timestamp}_{content_hash}"
