"""
Tests for LocalSink.
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from sim_sdk.sink.local import LocalSink


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    dirpath = tempfile.mkdtemp()
    yield dirpath
    shutil.rmtree(dirpath)


def test_local_sink_creation(temp_dir):
    """Test creating a LocalSink."""
    sink = LocalSink(output_dir=temp_dir)
    assert sink.output_dir == Path(temp_dir)
    assert sink.output_dir.exists()


def test_local_sink_write(temp_dir):
    """Test writing a fixture to LocalSink."""
    sink = LocalSink(output_dir=temp_dir)
    
    fixture_data = {
        "fixture_id": "test_123",
        "data": {"key": "value"}
    }
    
    sink.write("test_123", fixture_data)
    
    # Check file was created
    fixture_file = Path(temp_dir) / "test_123.json"
    assert fixture_file.exists()
    
    # Check content
    with open(fixture_file) as f:
        loaded_data = json.load(f)
    
    assert loaded_data == fixture_data


def test_local_sink_multiple_writes(temp_dir):
    """Test writing multiple fixtures."""
    sink = LocalSink(output_dir=temp_dir)
    
    for i in range(3):
        fixture_data = {"fixture_id": f"test_{i}", "value": i}
        sink.write(f"test_{i}", fixture_data)
    
    # Check all files were created
    files = list(Path(temp_dir).glob("*.json"))
    assert len(files) == 3


def test_local_sink_flush(temp_dir):
    """Test that flush is a no-op for LocalSink."""
    sink = LocalSink(output_dir=temp_dir)
    sink.flush()  # Should not raise


def test_local_sink_close(temp_dir):
    """Test that close is a no-op for LocalSink."""
    sink = LocalSink(output_dir=temp_dir)
    sink.close()  # Should not raise
