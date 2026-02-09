"""Tests for sim_sdk.canonicalize module."""

from decimal import Decimal

import pytest

from sim_sdk.canonicalize import (
    canonicalize,
    fingerprint,
    fingerprint_request,
    fingerprint_sql,
)


class TestCanonicalize:
    """Tests for the canonicalize function."""

    def test_simple_dict(self):
        data = {"b": 2, "a": 1}
        result = canonicalize(data)
        assert result == '{"a":1,"b":2}'

    def test_nested_dict_ordering(self):
        # Same data with different key orders should produce same result
        data1 = {"outer": {"b": 2, "a": 1}, "name": "test"}
        data2 = {"name": "test", "outer": {"a": 1, "b": 2}}

        assert canonicalize(data1) == canonicalize(data2)

    def test_float_normalization(self):
        # Floats should be rounded to 6 decimal places
        data = {"value": 1.23456789}
        result = canonicalize(data)
        assert result == '{"value":1.234568}'

    def test_float_small_differences_normalized(self):
        # Very small float differences should normalize to same value
        data1 = {"value": 1.0000001}
        data2 = {"value": 1.0000002}

        assert canonicalize(data1) == canonicalize(data2)

    def test_decimal_converted_to_float(self):
        data = {"price": Decimal("19.99")}
        result = canonicalize(data)
        assert "19.99" in result

    def test_none_value(self):
        data = {"value": None}
        result = canonicalize(data)
        assert result == '{"value":null}'

    def test_boolean_values(self):
        data = {"true_val": True, "false_val": False}
        result = canonicalize(data)
        assert result == '{"false_val":false,"true_val":true}'

    def test_list_ordering_preserved(self):
        data = {"items": [3, 1, 2]}
        result = canonicalize(data)
        assert result == '{"items":[3,1,2]}'

    def test_bytes_converted_to_base64(self):
        data = {"data": b"hello"}
        result = canonicalize(data)
        # "hello" in base64 is "aGVsbG8="
        assert "aGVsbG8=" in result

    def test_special_floats(self):
        # NaN becomes null
        data_nan = {"value": float("nan")}
        assert '"value":null' in canonicalize(data_nan)

        # Infinity becomes string
        data_inf = {"value": float("inf")}
        assert '"value":"Infinity"' in canonicalize(data_inf)

        data_neg_inf = {"value": float("-inf")}
        assert '"value":"-Infinity"' in canonicalize(data_neg_inf)


class TestFingerprint:
    """Tests for the fingerprint function."""

    def test_deterministic(self):
        data = {"name": "test", "value": 123}

        fp1 = fingerprint(data)
        fp2 = fingerprint(data)

        assert fp1 == fp2

    def test_length(self):
        data = {"name": "test"}
        fp = fingerprint(data)

        assert len(fp) == 16  # First 16 chars of SHA256

    def test_different_data_different_fingerprint(self):
        fp1 = fingerprint({"a": 1})
        fp2 = fingerprint({"a": 2})

        assert fp1 != fp2

    def test_key_order_independent(self):
        fp1 = fingerprint({"b": 2, "a": 1})
        fp2 = fingerprint({"a": 1, "b": 2})

        assert fp1 == fp2

    def test_handles_nested_structures(self):
        data = {
            "outer": {
                "inner": [1, 2, {"deep": "value"}]
            }
        }
        fp = fingerprint(data)
        assert len(fp) == 16


class TestFingerprintRequest:
    """Tests for the fingerprint_request function."""

    def test_basic_request(self):
        fp = fingerprint_request(
            method="POST",
            path="/api/quote",
            body={"items": ["A", "B"]},
        )
        assert len(fp) == 16

    def test_method_case_insensitive(self):
        fp1 = fingerprint_request(method="POST", path="/api")
        fp2 = fingerprint_request(method="post", path="/api")

        assert fp1 == fp2

    def test_with_headers(self):
        fp1 = fingerprint_request(
            method="GET",
            path="/api",
            headers={"content-type": "application/json", "x-custom": "value"},
            header_keys=["content-type"],
        )
        fp2 = fingerprint_request(
            method="GET",
            path="/api",
            headers={"content-type": "application/json", "x-other": "different"},
            header_keys=["content-type"],
        )

        # Same content-type, different excluded headers should match
        assert fp1 == fp2

    def test_body_affects_fingerprint(self):
        fp1 = fingerprint_request(method="POST", path="/api", body={"a": 1})
        fp2 = fingerprint_request(method="POST", path="/api", body={"a": 2})

        assert fp1 != fp2


class TestFingerprintSql:
    """Tests for the fingerprint_sql function."""

    def test_basic_query(self):
        fp = fingerprint_sql("SELECT * FROM users WHERE id = %s", (123,))
        assert len(fp) == 16

    def test_whitespace_normalized(self):
        fp1 = fingerprint_sql("SELECT * FROM users", None)
        fp2 = fingerprint_sql("SELECT  *  FROM   users", None)

        assert fp1 == fp2

    def test_params_affect_fingerprint(self):
        fp1 = fingerprint_sql("SELECT * FROM users WHERE id = %s", (1,))
        fp2 = fingerprint_sql("SELECT * FROM users WHERE id = %s", (2,))

        assert fp1 != fp2

    def test_dict_params(self):
        fp = fingerprint_sql(
            "SELECT * FROM users WHERE name = %(name)s",
            {"name": "test"},
        )
        assert len(fp) == 16
