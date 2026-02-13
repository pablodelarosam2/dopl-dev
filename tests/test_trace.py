"""
Tests for @sim_trace decorator using plain Python functions.
"""

import pytest
from sim_sdk import sim_trace
from sim_sdk.context import SimContext


def test_trace_without_context():
    """Test that @sim_trace works without a recording context."""
    
    @sim_trace
    def add(a, b):
        return a + b
    
    result = add(2, 3)
    assert result == 5


def test_trace_with_context():
    """Test that @sim_trace records calls when context is active."""
    
    @sim_trace
    def multiply(a, b):
        return a * b
    
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    
    try:
        result = multiply(4, 5)
        assert result == 20
        # TODO: Assert that trace was recorded in context
    finally:
        SimContext.reset_current(token)


def test_trace_with_exception():
    """Test that @sim_trace handles exceptions properly."""
    
    @sim_trace
    def failing_function():
        raise ValueError("Test error")
    
    with pytest.raises(ValueError, match="Test error"):
        failing_function()


def test_trace_nested_calls():
    """Test tracing nested function calls."""
    
    @sim_trace
    def inner(x):
        return x * 2
    
    @sim_trace
    def outer(x):
        return inner(x) + 1
    
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    
    try:
        result = outer(5)
        assert result == 11
        # TODO: Assert that both traces were recorded
    finally:
        SimContext.reset_current(token)
