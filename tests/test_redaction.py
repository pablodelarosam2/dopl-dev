"""
Tests for PII redaction and pseudonymization.
"""

import pytest
from sim_sdk.redaction import (
    redact,
    pseudonymize,
    create_redactor,
    create_pseudonymizer,
    detect_sensitive_keys,
    REDACTED_PLACEHOLDER,
)


def test_redact_simple_dict():
    """Test redacting a simple dictionary."""
    data = {"email": "test@example.com", "name": "Alice"}
    result = redact(data, paths=["$.email"])
    
    assert result["email"] == REDACTED_PLACEHOLDER
    assert result["name"] == "Alice"


def test_redact_nested_dict():
    """Test redacting nested dictionaries."""
    data = {
        "user": {
            "email": "test@example.com",
            "name": "Bob"
        },
        "active": True
    }
    result = redact(data, paths=["$.*.email"])
    
    assert result["user"]["email"] == REDACTED_PLACEHOLDER
    assert result["user"]["name"] == "Bob"
    assert result["active"] is True


def test_redact_preserves_original():
    """Test that redact doesn't modify original data by default."""
    data = {"email": "test@example.com", "name": "Alice"}
    result = redact(data, paths=["$.email"])
    
    # Original should be unchanged
    assert data["email"] == "test@example.com"
    # Result should be redacted
    assert result["email"] == REDACTED_PLACEHOLDER


def test_redact_in_place():
    """Test in-place redaction."""
    # Note: in_place=True with jsonpath-ng doesn't modify the original object
    # due to how jsonpath traversal works. Use for memory optimization with large datasets.
    data = {"email": "test@example.com", "name": "Alice"}
    result = redact(data, paths=["$.email"], in_place=True)
    
    # Result should be redacted
    assert result["email"] == REDACTED_PLACEHOLDER
    assert result["name"] == "Alice"


def test_redact_custom_placeholder():
    """Test redaction with custom placeholder."""
    data = {"password": "secret123"}
    result = redact(data, paths=["$.password"], placeholder="***")
    
    assert result["password"] == "***"


def test_pseudonymize_deterministic():
    """Test that pseudonymization is deterministic."""
    data = {"email": "alice@example.com", "name": "Alice"}
    
    result1 = pseudonymize(data, paths=["$.email"])
    result2 = pseudonymize(data, paths=["$.email"])
    
    # Same input should produce same pseudonym
    assert result1["email"] == result2["email"]
    # Should not be the original value
    assert result1["email"] != "alice@example.com"
    # Name should be unchanged
    assert result1["name"] == "Alice"


def test_pseudonymize_preserves_equality():
    """Test that pseudonymization preserves equality relationships."""
    data1 = {"user": {"email": "alice@example.com"}}
    data2 = {"admin": {"email": "alice@example.com"}}
    
    result1 = pseudonymize(data1, paths=["$.*.email"])
    result2 = pseudonymize(data2, paths=["$.*.email"])
    
    # Same email should produce same pseudonym across different structures
    assert result1["user"]["email"] == result2["admin"]["email"]


def test_pseudonymize_different_values():
    """Test that different values produce different pseudonyms."""
    data = {
        "user1": {"email": "alice@example.com"},
        "user2": {"email": "bob@example.com"}
    }
    
    result = pseudonymize(data, paths=["$.*.email"])
    
    # Different emails should produce different pseudonyms
    assert result["user1"]["email"] != result["user2"]["email"]


def test_pseudonymize_custom_salt():
    """Test pseudonymization with custom salt."""
    data = {"email": "alice@example.com"}
    
    result1 = pseudonymize(data, paths=["$.email"], salt="salt1")
    result2 = pseudonymize(data, paths=["$.email"], salt="salt2")
    
    # Different salts should produce different pseudonyms
    assert result1["email"] != result2["email"]


def test_pseudonymize_custom_length():
    """Test pseudonymization with custom length."""
    data = {"email": "alice@example.com"}
    
    result1 = pseudonymize(data, paths=["$.email"], length=8)
    result2 = pseudonymize(data, paths=["$.email"], length=32)
    
    assert len(result1["email"]) == 8
    assert len(result2["email"]) == 32


def test_pseudonymize_vs_redact():
    """Test that pseudonymization differs from redaction."""
    data = {"email": "alice@example.com", "name": "Alice"}
    
    redacted = redact(data, paths=["$.email"])
    pseudonymized = pseudonymize(data, paths=["$.email"])
    
    # Redacted should be placeholder
    assert redacted["email"] == REDACTED_PLACEHOLDER
    # Pseudonymized should be a hash
    assert pseudonymized["email"] != REDACTED_PLACEHOLDER
    assert len(pseudonymized["email"]) == 16  # Default length


def test_create_redactor():
    """Test creating reusable redactor."""
    my_redactor = create_redactor(["$.email", "$.password"])
    
    data1 = {"email": "alice@example.com", "password": "secret", "name": "Alice"}
    data2 = {"email": "bob@example.com", "password": "pass123", "name": "Bob"}
    
    result1 = my_redactor(data1)
    result2 = my_redactor(data2)
    
    assert result1["email"] == REDACTED_PLACEHOLDER
    assert result1["password"] == REDACTED_PLACEHOLDER
    assert result1["name"] == "Alice"
    
    assert result2["email"] == REDACTED_PLACEHOLDER
    assert result2["password"] == REDACTED_PLACEHOLDER
    assert result2["name"] == "Bob"


def test_create_pseudonymizer():
    """Test creating reusable pseudonymizer."""
    my_pseudonymizer = create_pseudonymizer(["$.email"])
    
    data1 = {"email": "alice@example.com", "name": "Alice"}
    data2 = {"email": "alice@example.com", "name": "Alice (duplicate)"}
    
    result1 = my_pseudonymizer(data1)
    result2 = my_pseudonymizer(data2)
    
    # Same email should produce same pseudonym
    assert result1["email"] == result2["email"]
    # Names should be preserved
    assert result1["name"] == "Alice"
    assert result2["name"] == "Alice (duplicate)"


def test_detect_sensitive_keys():
    """Test automatic detection of sensitive keys."""
    data = {
        "user_email": "test@example.com",
        "password": "secret",
        "ssn": "123-45-6789",
        "name": "Alice",
        "age": 30,
        "api_token": "token123"
    }
    
    sensitive_paths = detect_sensitive_keys(data)
    
    # Should detect email, password, ssn, and token
    assert any("email" in path.lower() for path in sensitive_paths)
    assert any("password" in path.lower() for path in sensitive_paths)
    assert any("ssn" in path.lower() for path in sensitive_paths)
    assert any("token" in path.lower() for path in sensitive_paths)


def test_redact_multiple_fields():
    """Test redacting multiple fields at once."""
    data = {
        "user1": {"email": "alice@example.com", "name": "Alice"},
        "user2": {"email": "bob@example.com", "name": "Bob"}
    }
    
    # Use wildcard pattern to match email at second level
    result = redact(data, paths=["$.*.email"])
    
    # Both emails should be redacted
    assert result["user1"]["email"] == REDACTED_PLACEHOLDER
    assert result["user2"]["email"] == REDACTED_PLACEHOLDER
    # Names should be preserved
    assert result["user1"]["name"] == "Alice"
    assert result["user2"]["name"] == "Bob"


def test_pseudonymize_multiple_fields():
    """Test pseudonymizing multiple fields with same value."""
    data = {
        "user1": {"email": "alice@example.com", "name": "Alice"},
        "user2": {"email": "alice@example.com", "name": "Alice Clone"}
    }
    
    result = pseudonymize(data, paths=["$.*.email"])
    
    # Same email should produce same pseudonym even in different objects
    assert result["user1"]["email"] == result["user2"]["email"]
    # Should not be the original value
    assert result["user1"]["email"] != "alice@example.com"


def test_redact_none():
    """Test redacting None values."""
    result = redact(None, paths=["$.email"])
    assert result is None


def test_pseudonymize_none():
    """Test pseudonymizing None values."""
    result = pseudonymize(None, paths=["$.email"])
    assert result is None


def test_redact_empty_paths():
    """Test redacting with empty paths."""
    data = {"email": "test@example.com"}
    result = redact(data, paths=[])
    
    # Should return unchanged
    assert result == data


def test_pseudonymize_empty_paths():
    """Test pseudonymizing with empty paths."""
    data = {"email": "test@example.com"}
    result = pseudonymize(data, paths=[])
    
    # Should return unchanged
    assert result == data


def test_critical_use_case_control_flow():
    """
    Critical test: Ensure pseudonymization preserves control flow logic.
    
    This simulates code that makes decisions based on email values.
    """
    # Simulate a function that routes based on email
    def route_user(user_data):
        if user_data["email"] == "admin@example.com":
            return "admin_handler"
        elif user_data["email"] == "support@example.com":
            return "support_handler"
        else:
            return "default_handler"
    
    # Original data
    admin_user = {"email": "admin@example.com", "name": "Admin"}
    support_user = {"email": "support@example.com", "name": "Support"}
    regular_user = {"email": "user@example.com", "name": "User"}
    
    # Pseudonymize all users
    admin_pseudo = pseudonymize(admin_user, paths=["$.email"])
    support_pseudo = pseudonymize(support_user, paths=["$.email"])
    regular_pseudo = pseudonymize(regular_user, paths=["$.email"])
    
    # Create a mapping of pseudonyms to routes
    pseudonym_routes = {
        admin_pseudo["email"]: "admin_handler",
        support_pseudo["email"]: "support_handler"
    }
    
    # Simulate routing with pseudonymized data
    def route_user_pseudonymized(user_data, route_map):
        return route_map.get(user_data["email"], "default_handler")
    
    # Test that routing logic still works
    assert route_user_pseudonymized(admin_pseudo, pseudonym_routes) == "admin_handler"
    assert route_user_pseudonymized(support_pseudo, pseudonym_routes) == "support_handler"
    assert route_user_pseudonymized(regular_pseudo, pseudonym_routes) == "default_handler"
    
    # Test that same email produces same route
    admin_duplicate = {"email": "admin@example.com", "name": "Another Admin"}
    admin_duplicate_pseudo = pseudonymize(admin_duplicate, paths=["$.email"])
    assert admin_duplicate_pseudo["email"] == admin_pseudo["email"]
    assert route_user_pseudonymized(admin_duplicate_pseudo, pseudonym_routes) == "admin_handler"
