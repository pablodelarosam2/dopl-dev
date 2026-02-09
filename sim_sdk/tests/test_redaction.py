"""Tests for sim_sdk.redaction module."""

import pytest

from sim_sdk.redaction import (
    DEFAULT_REDACT_PATHS,
    REDACTED_PLACEHOLDER,
    create_redactor,
    detect_sensitive_keys,
    redact,
)


class TestRedact:
    """Tests for the redact function."""

    def test_simple_redaction(self):
        data = {"user": {"email": "test@example.com", "name": "John"}}
        result = redact(data, paths=["$.user.email"])

        assert result["user"]["email"] == REDACTED_PLACEHOLDER
        assert result["user"]["name"] == "John"

    def test_wildcard_redaction(self):
        data = {
            "user1": {"email": "a@example.com"},
            "user2": {"email": "b@example.com"},
        }
        result = redact(data, paths=["$.*.email"])

        assert result["user1"]["email"] == REDACTED_PLACEHOLDER
        assert result["user2"]["email"] == REDACTED_PLACEHOLDER

    def test_nested_redaction(self):
        data = {
            "response": {
                "data": {
                    "user": {
                        "password": "secret123"
                    }
                }
            }
        }
        result = redact(data, paths=["$.response.data.user.password"])

        assert result["response"]["data"]["user"]["password"] == REDACTED_PLACEHOLDER

    def test_array_redaction(self):
        data = {
            "users": [
                {"email": "a@example.com"},
                {"email": "b@example.com"},
            ]
        }
        result = redact(data, paths=["$.users[*].email"])

        assert result["users"][0]["email"] == REDACTED_PLACEHOLDER
        assert result["users"][1]["email"] == REDACTED_PLACEHOLDER

    def test_no_paths_returns_original(self):
        data = {"email": "test@example.com"}
        result = redact(data, paths=[])

        assert result == data

    def test_none_data_returns_none(self):
        assert redact(None) is None

    def test_non_dict_returns_original(self):
        assert redact("string") == "string"
        assert redact(123) == 123

    def test_custom_placeholder(self):
        data = {"secret": "value"}
        result = redact(data, paths=["$.secret"], placeholder="***")

        assert result["secret"] == "***"

    def test_in_place_modification(self):
        data = {"secret": "value"}
        original_id = id(data)
        result = redact(data, paths=["$.secret"], in_place=True)

        assert id(result) == original_id
        assert data["secret"] == REDACTED_PLACEHOLDER

    def test_deep_copy_by_default(self):
        data = {"secret": "value"}
        result = redact(data, paths=["$.secret"])

        assert result["secret"] == REDACTED_PLACEHOLDER
        assert data["secret"] == "value"  # Original unchanged

    def test_invalid_path_skipped(self):
        data = {"email": "test@example.com"}
        # Invalid JSONPath syntax should be skipped
        result = redact(data, paths=["invalid[[[path"])

        assert result == data

    def test_default_paths(self):
        # Verify DEFAULT_REDACT_PATHS exists and has expected entries
        assert "$.*.email" in DEFAULT_REDACT_PATHS
        assert "$.*.password" in DEFAULT_REDACT_PATHS


class TestCreateRedactor:
    """Tests for the create_redactor function."""

    def test_create_reusable_redactor(self):
        my_redactor = create_redactor(["$.email", "$.ssn"])

        data1 = {"email": "a@example.com", "name": "Alice"}
        data2 = {"email": "b@example.com", "ssn": "123-45-6789"}

        result1 = my_redactor(data1)
        result2 = my_redactor(data2)

        assert result1["email"] == REDACTED_PLACEHOLDER
        assert result1["name"] == "Alice"

        assert result2["email"] == REDACTED_PLACEHOLDER
        assert result2["ssn"] == REDACTED_PLACEHOLDER

    def test_custom_placeholder_in_redactor(self):
        my_redactor = create_redactor(["$.secret"], placeholder="HIDDEN")

        data = {"secret": "value"}
        result = my_redactor(data)

        assert result["secret"] == "HIDDEN"


class TestDetectSensitiveKeys:
    """Tests for the detect_sensitive_keys function."""

    def test_detects_email(self):
        data = {"user_email": "test@example.com"}
        found = detect_sensitive_keys(data)

        assert any("email" in path.lower() for path in found)

    def test_detects_password(self):
        data = {"password": "secret", "user_password": "secret2"}
        found = detect_sensitive_keys(data)

        assert len(found) >= 2
        assert any("password" in path.lower() for path in found)

    def test_detects_nested_sensitive(self):
        data = {
            "user": {
                "email": "test@example.com",
                "profile": {
                    "phone_number": "555-1234"
                }
            }
        }
        found = detect_sensitive_keys(data)

        assert any("email" in path for path in found)
        assert any("phone" in path for path in found)

    def test_detects_in_arrays(self):
        data = {
            "users": [
                {"email": "a@example.com"},
                {"email": "b@example.com"},
            ]
        }
        found = detect_sensitive_keys(data)

        assert any("email" in path for path in found)

    def test_common_sensitive_patterns(self):
        data = {
            "api_key": "xxx",
            "auth_token": "yyy",
            "card_number": "1234",
            "social_security": "000-00-0000",
        }
        found = detect_sensitive_keys(data)

        # Should find all of these
        assert len(found) >= 4

    def test_empty_data(self):
        assert detect_sensitive_keys({}) == []
        assert detect_sensitive_keys([]) == []
