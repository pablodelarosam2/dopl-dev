"""Tests for the diff engine."""

import pytest
from sim_sdk.diff import (
    DiffEngine,
    DiffConfig,
    DiffResult,
    Difference,
    DiffType,
    SimulationReport,
    compare_responses,
)


class TestDiffEngine:
    """Test the DiffEngine class."""

    def test_identical_responses_pass(self):
        """Test that identical responses pass."""
        engine = DiffEngine()
        golden = {"status": 200, "body": {"total": 21.78, "items": ["a", "b"]}}
        candidate = {"status": 200, "body": {"total": 21.78, "items": ["a", "b"]}}

        result = engine.compare("test-001", "/quote", golden, candidate)

        assert result.passed is True
        assert result.has_regressions is False
        assert len(result.differences) == 0

    def test_status_code_difference(self):
        """Test that status code differences are detected."""
        engine = DiffEngine()
        golden = {"status": 200, "body": {}}
        candidate = {"status": 500, "body": {}}

        result = engine.compare("test-001", "/quote", golden, candidate)

        assert result.passed is False
        assert len(result.differences) == 1
        assert result.differences[0].diff_type == DiffType.STATUS_CODE
        assert result.differences[0].golden_value == 200
        assert result.differences[0].candidate_value == 500

    def test_value_changed_detected(self):
        """Test that value changes are detected."""
        engine = DiffEngine()
        golden = {"status": 200, "body": {"name": "Product A"}}
        candidate = {"status": 200, "body": {"name": "Product B"}}

        result = engine.compare("test-001", "/quote", golden, candidate)

        assert result.passed is False
        assert len(result.differences) == 1
        assert result.differences[0].diff_type == DiffType.VALUE_CHANGED
        assert result.differences[0].path == "name"
        assert result.differences[0].golden_value == "Product A"
        assert result.differences[0].candidate_value == "Product B"

    def test_item_added_detected(self):
        """Test that new fields are detected."""
        engine = DiffEngine()
        golden = {"status": 200, "body": {"name": "Test"}}
        candidate = {"status": 200, "body": {"name": "Test", "extra": "field"}}

        result = engine.compare("test-001", "/quote", golden, candidate)

        assert result.passed is False
        assert any(d.diff_type == DiffType.ITEM_ADDED for d in result.differences)

    def test_item_removed_detected(self):
        """Test that removed fields are detected."""
        engine = DiffEngine()
        golden = {"status": 200, "body": {"name": "Test", "price": 10}}
        candidate = {"status": 200, "body": {"name": "Test"}}

        result = engine.compare("test-001", "/quote", golden, candidate)

        assert result.passed is False
        assert any(d.diff_type == DiffType.ITEM_REMOVED for d in result.differences)


class TestMoneyTolerance:
    """Test money tolerance handling."""

    def test_within_tolerance_passes(self):
        """Test that money differences within tolerance pass."""
        config = DiffConfig(money_paths=["total"], money_tolerance=0.01)
        engine = DiffEngine(config)

        golden = {"status": 200, "body": {"total": 21.78}}
        candidate = {"status": 200, "body": {"total": 21.785}}  # 0.005 diff

        result = engine.compare("test-001", "/quote", golden, candidate)

        assert result.passed is True
        assert "total" in str(result.ignored_paths)

    def test_exceeds_tolerance_fails(self):
        """Test that money differences exceeding tolerance fail."""
        config = DiffConfig(money_paths=["total"], money_tolerance=0.01)
        engine = DiffEngine(config)

        golden = {"status": 200, "body": {"total": 21.78}}
        candidate = {"status": 200, "body": {"total": 21.80}}  # 0.02 diff

        result = engine.compare("test-001", "/quote", golden, candidate)

        assert result.passed is False
        assert result.differences[0].diff_type == DiffType.MONEY_TOLERANCE_EXCEEDED

    def test_default_money_paths(self):
        """Test that default money paths are recognized."""
        engine = DiffEngine()  # Uses default config

        golden = {"status": 200, "body": {"subtotal": 100.00, "tax": 8.00, "total": 108.00}}
        candidate = {"status": 200, "body": {"subtotal": 100.005, "tax": 8.005, "total": 108.005}}

        result = engine.compare("test-001", "/quote", golden, candidate)

        # All within 0.01 tolerance
        assert result.passed is True


class TestIgnorePaths:
    """Test ignore path handling."""

    def test_ignored_fields_not_compared(self):
        """Test that ignored fields don't cause failures."""
        config = DiffConfig(ignore_paths=["request_id", "timestamp"])
        engine = DiffEngine(config)

        golden = {"status": 200, "body": {"data": "same", "request_id": "abc", "timestamp": "2024-01-01"}}
        candidate = {"status": 200, "body": {"data": "same", "request_id": "xyz", "timestamp": "2024-01-02"}}

        result = engine.compare("test-001", "/quote", golden, candidate)

        assert result.passed is True
        assert "request_id" in str(result.ignored_paths)
        assert "timestamp" in str(result.ignored_paths)

    def test_nested_ignored_fields(self):
        """Test that nested ignored fields work."""
        config = DiffConfig(ignore_paths=["trace_id"])
        engine = DiffEngine(config)

        golden = {"status": 200, "body": {"data": {"trace_id": "abc"}}}
        candidate = {"status": 200, "body": {"data": {"trace_id": "xyz"}}}

        result = engine.compare("test-001", "/quote", golden, candidate)

        assert result.passed is True


class TestDiffResult:
    """Test DiffResult methods."""

    def test_to_dict(self):
        """Test serialization to dict."""
        result = DiffResult(
            fixture_id="test-001",
            endpoint="/quote",
            passed=False,
            differences=[
                Difference(
                    diff_type=DiffType.VALUE_CHANGED,
                    path="total",
                    golden_value=100,
                    candidate_value=200,
                    message="Value changed",
                )
            ],
        )

        d = result.to_dict()

        assert d["fixture_id"] == "test-001"
        assert d["passed"] is False
        assert len(d["differences"]) == 1
        assert d["differences"][0]["type"] == "value_changed"

    def test_has_regressions_property(self):
        """Test has_regressions property."""
        result_pass = DiffResult(fixture_id="a", endpoint="/", passed=True, differences=[])
        result_fail = DiffResult(
            fixture_id="b",
            endpoint="/",
            passed=False,
            differences=[Difference(DiffType.VALUE_CHANGED, "x", 1, 2, "msg")],
        )

        assert result_pass.has_regressions is False
        assert result_fail.has_regressions is True


class TestSimulationReport:
    """Test SimulationReport class."""

    def test_passed_property(self):
        """Test the passed property."""
        report_pass = SimulationReport(
            run_id="run-001",
            candidate_image="test:latest",
            total_fixtures=5,
            passed_fixtures=5,
            failed_fixtures=0,
        )

        report_fail = SimulationReport(
            run_id="run-001",
            candidate_image="test:latest",
            total_fixtures=5,
            passed_fixtures=4,
            failed_fixtures=1,
        )

        report_stub_miss = SimulationReport(
            run_id="run-001",
            candidate_image="test:latest",
            total_fixtures=5,
            passed_fixtures=5,
            failed_fixtures=0,
            stub_misses=[{"type": "db", "details": "missing stub"}],
        )

        assert report_pass.passed is True
        assert report_fail.passed is False
        assert report_stub_miss.passed is False

    def test_to_markdown_passed(self):
        """Test markdown generation for passing report."""
        report = SimulationReport(
            run_id="run-001",
            candidate_image="myapp:pr-123",
            total_fixtures=10,
            passed_fixtures=10,
            failed_fixtures=0,
        )

        md = report.to_markdown()

        assert "# Simulation Report" in md
        assert "run-001" in md
        assert "myapp:pr-123" in md
        assert "PASSED" in md
        assert "No regressions detected" in md

    def test_to_markdown_failed(self):
        """Test markdown generation for failing report."""
        result = DiffResult(
            fixture_id="req-001",
            endpoint="/quote",
            passed=False,
            differences=[
                Difference(
                    diff_type=DiffType.VALUE_CHANGED,
                    path="total",
                    golden_value=100.00,
                    candidate_value=99.00,
                    message="Value changed from 100.0 to 99.0",
                )
            ],
        )

        report = SimulationReport(
            run_id="run-001",
            candidate_image="myapp:pr-123",
            total_fixtures=10,
            passed_fixtures=9,
            failed_fixtures=1,
            results=[result],
        )

        md = report.to_markdown()

        assert "FAILED" in md
        assert "Regressions Detected" in md
        assert "/quote" in md
        assert "req-001" in md
        assert "total" in md

    def test_to_dict(self):
        """Test serialization to dict."""
        report = SimulationReport(
            run_id="run-001",
            candidate_image="test:latest",
            total_fixtures=5,
            passed_fixtures=5,
            failed_fixtures=0,
        )

        d = report.to_dict()

        assert d["run_id"] == "run-001"
        assert d["passed"] is True
        assert d["total_fixtures"] == 5


class TestCompareResponsesConvenience:
    """Test the compare_responses convenience function."""

    def test_basic_comparison(self):
        """Test basic comparison using convenience function."""
        golden = {"status": 200, "body": {"value": 42}}
        candidate = {"status": 200, "body": {"value": 42}}

        result = compare_responses(golden, candidate)

        assert result.passed is True

    def test_with_custom_config(self):
        """Test comparison with custom config."""
        config = DiffConfig(ignore_paths=["id"])
        golden = {"body": {"id": "abc", "name": "test"}}
        candidate = {"body": {"id": "xyz", "name": "test"}}

        result = compare_responses(golden, candidate, config=config)

        assert result.passed is True


class TestRealWorldScenarios:
    """Test real-world regression scenarios."""

    def test_pricing_regression(self):
        """Test detection of a pricing calculation regression."""
        engine = DiffEngine()

        # Golden: correct calculation
        golden = {
            "status": 200,
            "body": {
                "user_id": 123,
                "items": [
                    {"sku": "A1", "qty": 2, "unit_price": 19.99, "line_total": 39.98},
                    {"sku": "B1", "qty": 1, "unit_price": 29.99, "line_total": 29.99},
                ],
                "subtotal": 69.97,
                "tax_rate": 0.0925,
                "tax": 6.47,
                "total": 76.44,
            },
        }

        # Candidate: buggy calculation (wrong tax)
        candidate = {
            "status": 200,
            "body": {
                "user_id": 123,
                "items": [
                    {"sku": "A1", "qty": 2, "unit_price": 19.99, "line_total": 39.98},
                    {"sku": "B1", "qty": 1, "unit_price": 29.99, "line_total": 29.99},
                ],
                "subtotal": 69.97,
                "tax_rate": 0.0925,
                "tax": 5.00,  # Bug: wrong tax calculation
                "total": 74.97,  # Bug: wrong total
            },
        }

        result = engine.compare("pricing-001", "/quote", golden, candidate)

        assert result.passed is False
        assert result.has_regressions is True

        # Should detect both tax and total differences
        paths_with_issues = [d.path for d in result.differences]
        assert "tax" in paths_with_issues
        assert "total" in paths_with_issues

    def test_missing_discount_field(self):
        """Test detection of accidentally removed field."""
        engine = DiffEngine()

        golden = {
            "status": 200,
            "body": {
                "subtotal": 100.00,
                "discount": 10.00,
                "total": 90.00,
            },
        }

        candidate = {
            "status": 200,
            "body": {
                "subtotal": 100.00,
                # discount field missing!
                "total": 100.00,  # Wrong because discount not applied
            },
        }

        result = engine.compare("discount-001", "/checkout", golden, candidate)

        assert result.passed is False

        # Should detect removed discount field and wrong total
        diff_types = [d.diff_type for d in result.differences]
        assert DiffType.ITEM_REMOVED in diff_types

    def test_experiment_metric_regression(self):
        """Test detection of experiment metric changes."""
        config = DiffConfig(
            ignore_paths=["request_id", "timestamp"],
            money_paths=["conversion_value"],
            money_tolerance=0.001,
        )
        engine = DiffEngine(config)

        golden = {
            "status": 200,
            "body": {
                "experiment_id": "exp_123",
                "variant": "treatment",
                "metrics": {
                    "conversion_rate": 0.15,
                    "conversion_value": 45.50,
                },
                "request_id": "req_abc",
            },
        }

        candidate = {
            "status": 200,
            "body": {
                "experiment_id": "exp_123",
                "variant": "treatment",
                "metrics": {
                    "conversion_rate": 0.12,  # Regression!
                    "conversion_value": 45.50,
                },
                "request_id": "req_xyz",  # Should be ignored
            },
        }

        result = engine.compare("exp-001", "/experiment", golden, candidate)

        assert result.passed is False
        # request_id change should be ignored
        assert not any(d.path == "request_id" for d in result.differences)
        # conversion_rate change should be detected
        assert any("conversion_rate" in d.path for d in result.differences)
