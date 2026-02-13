"""
Tests for SimContext and contextvars management.
"""

import pytest
from sim_sdk.context import SimContext


def test_context_creation():
    """Test creating a new SimContext."""
    ctx = SimContext()
    assert ctx is not None


def test_context_get_current_none():
    """Test getting current context when none is set."""
    ctx = SimContext.get_current()
    assert ctx is None


def test_context_set_and_get():
    """Test setting and getting current context."""
    ctx = SimContext()
    token = SimContext.set_current(ctx)
    
    try:
        current = SimContext.get_current()
        assert current is ctx
    finally:
        SimContext.reset_current(token)


def test_context_isolation():
    """Test that contexts are properly isolated."""
    ctx1 = SimContext()
    ctx2 = SimContext()
    
    token1 = SimContext.set_current(ctx1)
    try:
        assert SimContext.get_current() is ctx1
        
        token2 = SimContext.set_current(ctx2)
        try:
            assert SimContext.get_current() is ctx2
        finally:
            SimContext.reset_current(token2)
        
        assert SimContext.get_current() is ctx1
    finally:
        SimContext.reset_current(token1)
