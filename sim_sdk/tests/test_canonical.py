"""
Tests for JSON canonicalization and fingerprinting.
"""

import pytest
from sim_sdk.canonical import (
    canonicalize_json,
    fingerprint,
    fingerprint_short,
    normalize_sql,
    fingerprint_sql,
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


def test_canonicalize_bytes():
    """Test canonicalizing bytes objects."""
    obj = {"data": b"hello world"}
    result = canonicalize_json(obj)
    
    # Should be base64 encoded
    assert "hello world" not in result
    assert "__bytes__" in result


def test_canonicalize_sets():
    """Test canonicalizing sets (should become sorted lists)."""
    obj = {"values": {3, 1, 2}}
    result = canonicalize_json(obj)
    
    # Should be sorted and deterministic
    assert result == '{"values":[1,2,3]}'


def test_canonicalize_datetime():
    """Test canonicalizing datetime objects."""
    from datetime import datetime
    
    dt = datetime(2026, 2, 13, 12, 0, 0)
    obj = {"timestamp": dt}
    result = canonicalize_json(obj)
    
    # Should use ISO format
    assert "2026-02-13" in result
    assert "12:00:00" in result


def test_normalize_sql_basic():
    """Test basic SQL normalization."""
    query1 = "SELECT * FROM users WHERE id = 1"
    query2 = "select  *  from users where id=1"
    query3 = "SELECT   *\nFROM\n  users\nWHERE id = 1"
    
    norm1 = normalize_sql(query1)
    norm2 = normalize_sql(query2)
    norm3 = normalize_sql(query3)
    
    # All should normalize to the same thing
    assert norm1 == norm2
    assert norm2 == norm3


def test_normalize_sql_comments():
    """Test SQL comment removal."""
    query_with_comments = """
    SELECT * FROM users -- this is a comment
    WHERE id = 1 /* multi-line
    comment */
    """
    
    normalized = normalize_sql(query_with_comments, strip_comments=True)
    
    # Comments should be removed
    assert "--" not in normalized
    assert "/*" not in normalized
    assert "*/" not in normalized
    # But query should still be valid
    assert "SELECT" in normalized
    assert "FROM" in normalized
    assert "users" in normalized


def test_normalize_sql_preserve_comments():
    """Test SQL normalization preserving comments."""
    query = "SELECT * FROM users -- important comment"
    
    normalized = normalize_sql(query, strip_comments=False)
    
    # Comment should be preserved (if using sqlparse) or removed (basic)
    # This test just verifies the flag is accepted
    assert "SELECT" in normalized


def test_fingerprint_sql_identical_queries():
    """Test that semantically identical SQL queries produce same fingerprint."""
    query1 = "SELECT * FROM users WHERE id = 1"
    query2 = "select  *  from users where id=1"
    query3 = "SELECT   *\nFROM\n  users\nWHERE id = 1"
    
    fp1 = fingerprint_sql(query1)
    fp2 = fingerprint_sql(query2)
    fp3 = fingerprint_sql(query3)
    
    # All should produce the same fingerprint
    assert fp1 == fp2
    assert fp2 == fp3
    assert len(fp1) == 64  # SHA-256 produces 64 hex characters


def test_fingerprint_sql_different_queries():
    """Test that different SQL queries produce different fingerprints."""
    query1 = "SELECT * FROM users WHERE id = 1"
    query2 = "SELECT * FROM users WHERE id = 2"
    query3 = "SELECT name FROM users WHERE id = 1"
    
    fp1 = fingerprint_sql(query1)
    fp2 = fingerprint_sql(query2)
    fp3 = fingerprint_sql(query3)
    
    # Different queries should produce different fingerprints
    assert fp1 != fp2
    assert fp2 != fp3
    assert fp1 != fp3


def test_normalize_sql_complex_query():
    """Test normalization of complex SQL query."""
    complex_query = """
    SELECT 
        u.id,
        u.name,
        COUNT(o.id) as order_count
    FROM users u
    LEFT JOIN orders o ON u.id = o.user_id
    WHERE u.active = true
    GROUP BY u.id, u.name
    ORDER BY order_count DESC
    LIMIT 10
    """
    
    normalized = normalize_sql(complex_query)
    
    # Should be normalized (keywords uppercase, whitespace collapsed)
    assert "SELECT" in normalized
    assert "FROM" in normalized
    assert "LEFT JOIN" in normalized or "LEFT" in normalized
    assert "GROUP BY" in normalized or "GROUP" in normalized
    assert "ORDER BY" in normalized or "ORDER" in normalized
    
    # Should not have excessive whitespace
    assert "\n\n" not in normalized


def test_normalize_sql_empty_or_none():
    """Test SQL normalization with edge cases."""
    assert normalize_sql("") == ""
    assert normalize_sql(None) is None
    assert normalize_sql("   ") == ""


def test_fingerprint_sql_with_parameters():
    """Test SQL fingerprinting with different parameter values."""
    # Same query structure with different values
    query1 = "SELECT * FROM users WHERE id = 1"
    query2 = "SELECT * FROM users WHERE id = 999"
    
    fp1 = fingerprint_sql(query1)
    fp2 = fingerprint_sql(query2)
    
    # Different parameter values should produce different fingerprints
    # This is correct behavior - we want to distinguish between queries
    # with different parameter values for proper fixture matching
    assert fp1 != fp2
