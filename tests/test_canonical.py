"""
Tests for JSON canonicalization and fingerprinting.
"""

import pytest
from sim_sdk.canonical import (
    canonicalize_json,
    fingerprint,
    fingerprint_short
)


def test_canonicalize_simple_dict():
    """Test canonicalizing a simple dictionary."""
    obj = {"name": "Alice", "age": 30}
    result = canonicalize_json(obj)
    
    # Keys should be sorted
    assert result == '{"age":30,"name":"Alice"}'


def test_canonicalize_nested_dict():
    """Test canonicalizing nested dictionaries."""
    obj = {
        "user": {"name": "Bob", "id": 123},
        "active": True
    }
    result = canonicalize_json(obj)
    
    # Should be deterministic
    assert '"active":true' in result
    assert '"user":' in result


def test_canonicalize_list():
    """Test canonicalizing lists."""
    obj = [3, 1, 2]
    result = canonicalize_json(obj)
    
    # Order should be preserved
    assert result == '[3,1,2]'


def test_canonicalize_deterministic():
    """Test that canonicalization is deterministic."""
    obj = {"z": 1, "a": 2, "m": 3}
    
    result1 = canonicalize_json(obj)
    result2 = canonicalize_json(obj)
    
    assert result1 == result2


def test_fingerprint_consistency():
    """Test that fingerprinting is consistent."""
    obj = {"data": "test", "value": 42}
    
    hash1 = fingerprint(obj)
    hash2 = fingerprint(obj)
    
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 produces 64 hex characters


def test_fingerprint_different_objects():
    """Test that different objects produce different fingerprints."""
    obj1 = {"data": "test1"}
    obj2 = {"data": "test2"}
    
    hash1 = fingerprint(obj1)
    hash2 = fingerprint(obj2)
    
    assert hash1 != hash2


def test_fingerprint_short():
    """Test short fingerprint generation."""
    obj = {"test": "data"}
    
    short = fingerprint_short(obj, length=16)
    full = fingerprint(obj)
    
    assert len(short) == 16
    assert full.startswith(short)


def test_fingerprint_order_independence():
    """Test that dict key order doesn't affect fingerprint."""
    obj1 = {"a": 1, "b": 2, "c": 3}
    obj2 = {"c": 3, "a": 1, "b": 2}
    
    hash1 = fingerprint(obj1)
    hash2 = fingerprint(obj2)
    
    assert hash1 == hash2
