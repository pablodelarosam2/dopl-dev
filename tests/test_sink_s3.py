"""
Tests for S3Sink.
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch
from sim_sdk.sink.s3 import S3Sink


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    dirpath = tempfile.mkdtemp()
    yield dirpath
    shutil.rmtree(dirpath)


def test_s3_sink_creation(temp_dir):
    """Test creating an S3Sink."""
    sink = S3Sink(
        bucket="test-bucket",
        prefix="fixtures/",
        local_buffer_dir=temp_dir
    )
    assert sink.bucket == "test-bucket"
    assert sink.prefix == "fixtures/"
    assert sink.local_buffer_dir == Path(temp_dir)


def test_s3_sink_write_to_buffer(temp_dir):
    """Test writing to local buffer."""
    sink = S3Sink(
        bucket="test-bucket",
        local_buffer_dir=temp_dir
    )
    
    fixture_data = {"fixture_id": "test_123", "data": "test"}
    sink.write("test_123", fixture_data)
    
    # Check file was written to buffer
    buffer_file = Path(temp_dir) / "test_123.json"
    assert buffer_file.exists()
    
    with open(buffer_file) as f:
        loaded_data = json.load(f)
    
    assert loaded_data == fixture_data


@patch('sim_sdk.sink.s3.boto3')
def test_s3_sink_flush_uploads(mock_boto3, temp_dir):
    """Test that flush uploads files to S3."""
    # Setup mock S3 client
    mock_client = Mock()
    mock_boto3.client.return_value = mock_client
    
    sink = S3Sink(
        bucket="test-bucket",
        prefix="fixtures/",
        local_buffer_dir=temp_dir,
        keep_local=False
    )
    
    # Write a fixture
    fixture_data = {"fixture_id": "test_123"}
    sink.write("test_123", fixture_data)
    
    # Flush should upload
    sink.flush()
    
    # Verify upload was called
    mock_client.upload_file.assert_called_once()
    call_args = mock_client.upload_file.call_args[0]
    assert call_args[1] == "test-bucket"
    assert call_args[2] == "fixtures/test_123.json"


@patch('sim_sdk.sink.s3.boto3')
def test_s3_sink_keep_local(mock_boto3, temp_dir):
    """Test that keep_local preserves files after upload."""
    mock_client = Mock()
    mock_boto3.client.return_value = mock_client
    
    sink = S3Sink(
        bucket="test-bucket",
        local_buffer_dir=temp_dir,
        keep_local=True
    )
    
    # Write and flush
    sink.write("test_123", {"data": "test"})
    sink.flush()
    
    # File should still exist
    buffer_file = Path(temp_dir) / "test_123.json"
    assert buffer_file.exists()


def test_s3_sink_boto3_import_error(temp_dir):
    """Test that ImportError is raised if boto3 is not available."""
    with patch('sim_sdk.sink.s3.boto3', None):
        sink = S3Sink(bucket="test-bucket", local_buffer_dir=temp_dir)
        
        # Write to buffer (should work)
        sink.write("test", {"data": "test"})
        
        # Accessing s3_client should raise ImportError
        with pytest.raises(ImportError, match="boto3 is required"):
            _ = sink.s3_client
