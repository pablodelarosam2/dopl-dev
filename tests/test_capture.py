"""
Tests for sim_capture() context manager.
"""

import pytest
from sim_sdk import sim_capture
from sim_sdk.context import SimContext


def test_capture_without_context():
    """Test that sim_capture works without a recording context."""
    
    with sim_capture("test_operation"):
        result = 2 + 2
    
    assert result == 4


def test_capture_with_context():
    """Test that sim_capture records when context is active."""
    
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    
    try:
        with sim_capture("api_call", endpoint="/users"):
            # Simulate some work
            data = {"user": "test"}
        
        # TODO: Assert that capture was recorded in context
    finally:
        SimContext.reset_current(token)


def test_capture_with_exception():
    """Test that sim_capture handles exceptions properly."""
    
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    
    try:
        with pytest.raises(ValueError, match="Test error"):
            with sim_capture("failing_operation"):
                raise ValueError("Test error")
        
        # TODO: Assert that exception was recorded
    finally:
        SimContext.reset_current(token)


def test_nested_captures():
    """Test nested sim_capture blocks."""
    
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    
    try:
        with sim_capture("outer"):
            with sim_capture("inner"):
                result = "nested"
        
        assert result == "nested"
        # TODO: Assert both captures were recorded
    finally:
        SimContext.reset_current(token)
