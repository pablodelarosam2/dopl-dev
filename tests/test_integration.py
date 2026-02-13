"""
End-to-end integration tests using plain Python functions.

No web server, just plain function calls with all 3 primitives.
"""

import pytest
import tempfile
import shutil
import json
from pathlib import Path
from sim_sdk import sim_trace, sim_capture, sim_db
from sim_sdk.context import SimContext
from sim_sdk.sink.local import LocalSink
from sim_sdk.fixture.writer import FixtureWriter
from sim_sdk.fixture.schema import Fixture


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    dirpath = tempfile.mkdtemp()
    yield dirpath
    shutil.rmtree(dirpath)


@sim_trace
def calculate_tax(amount, rate):
    """Example traced function."""
    return amount * rate


@sim_trace
def process_payment(amount):
    """Example function that calls other traced functions."""
    tax = calculate_tax(amount, 0.1)
    total = amount + tax
    return total


def test_e2e_trace_only(temp_dir):
    """Test end-to-end with just @sim_trace."""
    # Setup recording
    sink = LocalSink(output_dir=temp_dir)
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    
    try:
        # Execute traced functions
        result = process_payment(100)
        assert result == 110
        
        # TODO: Verify traces were recorded
    finally:
        SimContext.reset_current(token)


def test_e2e_all_primitives(temp_dir):
    """Test end-to-end with all 3 primitives: trace, capture, db."""
    # Setup recording
    sink = LocalSink(output_dir=temp_dir)
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    
    try:
        # Use all primitives
        with sim_capture("payment_flow"):
            amount = 100
            
            # Traced function
            total = process_payment(amount)
            
            # Mock database operation
            class MockDB:
                def cursor(self):
                    return self
                def execute(self, query, params=None):
                    pass
                def fetchone(self):
                    return (1, "success")
            
            with sim_db(MockDB(), name="payments") as db:
                cursor = db.cursor()
                cursor.execute("INSERT INTO payments (amount) VALUES (%s)", (total,))
        
        # TODO: Write fixture and verify all operations were recorded
    finally:
        SimContext.reset_current(token)


def test_e2e_with_fixture_writer(temp_dir):
    """Test complete flow with fixture writing."""
    # Setup
    sink = LocalSink(output_dir=temp_dir)
    writer = FixtureWriter(sink)
    
    # Create a simple fixture
    fixture = Fixture(
        fixture_id="test_fixture",
        timestamp="2024-01-01T00:00:00Z",
        metadata={"test": True}
    )
    
    # Write fixture
    fixture_id = writer.write_fixture(fixture)
    assert fixture_id == "test_fixture"
    
    # Verify file exists
    fixture_file = Path(temp_dir) / "test_fixture.json"
    assert fixture_file.exists()
    
    # Verify content
    with open(fixture_file) as f:
        data = json.load(f)
    
    assert data["fixture_id"] == "test_fixture"
    assert data["metadata"]["test"] is True
