"""
Minimal V0 Diff Engine for comparing candidate responses against golden outputs.

Features:
- Status code comparison
- JSON deep-diff
- Money tolerance (configurable absolute threshold)
- Ignore paths for non-deterministic fields (request_id, timestamp, etc.)

The diff engine takes two response sets as input and does not know their source.
Whether they come from a golden snapshot file or a live baseline container is transparent.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from deepdiff import DeepDiff


class DiffType(Enum):
    """Types of differences detected."""
    STATUS_CODE = "status_code"
    VALUE_CHANGED = "value_changed"
    TYPE_CHANGED = "type_changed"
    ITEM_ADDED = "item_added"
    ITEM_REMOVED = "item_removed"
    MONEY_TOLERANCE_EXCEEDED = "money_tolerance_exceeded"


@dataclass
class Difference:
    """A single difference between golden and candidate."""
    diff_type: DiffType
    path: str
    golden_value: Any
    candidate_value: Any
    message: str
    is_critical: bool = True  # All V0 diffs are critical (no WARN level)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.diff_type.value,
            "path": self.path,
            "golden": self.golden_value,
            "candidate": self.candidate_value,
            "message": self.message,
            "critical": self.is_critical,
        }


@dataclass
class DiffResult:
    """Result of comparing golden vs candidate response."""
    fixture_id: str
    endpoint: str
    passed: bool
    differences: List[Difference] = field(default_factory=list)
    ignored_paths: List[str] = field(default_factory=list)

    @property
    def has_regressions(self) -> bool:
        return len(self.differences) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "endpoint": self.endpoint,
            "passed": self.passed,
            "differences": [d.to_dict() for d in self.differences],
            "ignored_paths": self.ignored_paths,
        }


@dataclass
class DiffConfig:
    """Configuration for the diff engine."""
    # Paths to ignore (JSONPath-like patterns)
    ignore_paths: List[str] = field(default_factory=lambda: [
        "request_id",
        "trace_id",
        "timestamp",
        "quoted_at",
        "created_at",
        "updated_at",
    ])

    # Paths that contain monetary values (for tolerance checking)
    money_paths: List[str] = field(default_factory=lambda: [
        "total",
        "subtotal",
        "tax",
        "price",
        "amount",
        "cost",
    ])

    # Absolute tolerance for money values
    money_tolerance: float = 0.01

    # Float tolerance for non-money numeric values
    float_tolerance: float = 1e-9


class DiffEngine:
    """
    Minimal V0 diff engine for comparing responses.

    Usage:
        config = DiffConfig(
            ignore_paths=["request_id", "timestamp"],
            money_paths=["total", "subtotal"],
            money_tolerance=0.01,
        )
        engine = DiffEngine(config)

        result = engine.compare(
            fixture_id="req_001",
            endpoint="/quote",
            golden={"status": 200, "body": {"total": 21.78}},
            candidate={"status": 200, "body": {"total": 21.79}},
        )

        if result.has_regressions:
            print(result.to_dict())
    """

    def __init__(self, config: Optional[DiffConfig] = None):
        self.config = config or DiffConfig()
        # Compile ignore patterns
        self._ignore_patterns = self._compile_patterns(self.config.ignore_paths)
        self._money_patterns = self._compile_patterns(self.config.money_paths)

    def _compile_patterns(self, paths: List[str]) -> List[re.Pattern]:
        """Compile path patterns to regex for matching."""
        patterns = []
        for path in paths:
            # Convert simple path names to regex that matches anywhere in the path
            # e.g., "timestamp" matches "root['timestamp']", "root['data']['timestamp']", etc.
            escaped = re.escape(path)
            pattern = re.compile(rf".*\['{escaped}'\]$|.*\['{escaped}'\]\[|^{escaped}$")
            patterns.append(pattern)
        return patterns

    def _should_ignore(self, path: str) -> bool:
        """Check if a path should be ignored."""
        for pattern in self._ignore_patterns:
            if pattern.search(path):
                return True
        return False

    def _is_money_path(self, path: str) -> bool:
        """Check if a path contains monetary values."""
        for pattern in self._money_patterns:
            if pattern.search(path):
                return True
        return False

    def _extract_path_key(self, deepdiff_path: str) -> str:
        """Extract a readable path from DeepDiff's path format."""
        # DeepDiff uses format like "root['body']['total']"
        # Convert to more readable "body.total"
        path = deepdiff_path.replace("root", "").replace("']['", ".").replace("['", "").replace("']", "")
        if path.startswith("."):
            path = path[1:]
        return path

    def compare(
        self,
        fixture_id: str,
        endpoint: str,
        golden: Dict[str, Any],
        candidate: Dict[str, Any],
    ) -> DiffResult:
        """
        Compare golden output against candidate output.

        Args:
            fixture_id: ID of the fixture being compared
            endpoint: Endpoint name (for reporting)
            golden: Golden/expected response dict with 'status' and 'body'
            candidate: Candidate response dict with 'status' and 'body'

        Returns:
            DiffResult with all detected differences
        """
        differences: List[Difference] = []
        ignored: List[str] = []

        # Compare status codes
        golden_status = golden.get("status", golden.get("status_code", 200))
        candidate_status = candidate.get("status", candidate.get("status_code", 200))

        if golden_status != candidate_status:
            differences.append(Difference(
                diff_type=DiffType.STATUS_CODE,
                path="status_code",
                golden_value=golden_status,
                candidate_value=candidate_status,
                message=f"Status code changed from {golden_status} to {candidate_status}",
            ))

        # Compare bodies
        golden_body = golden.get("body", golden.get("output", golden))
        candidate_body = candidate.get("body", candidate.get("output", candidate))

        # Use DeepDiff for structural comparison
        diff = DeepDiff(
            golden_body,
            candidate_body,
            ignore_order=True,
            significant_digits=10,  # High precision, we'll handle tolerances ourselves
            verbose_level=2,
        )

        # Process value changes
        for change_type, changes in diff.items():
            if change_type == "values_changed":
                for path, change in changes.items():
                    readable_path = self._extract_path_key(path)

                    # Check if should ignore
                    if self._should_ignore(path):
                        ignored.append(readable_path)
                        continue

                    old_val = change.get("old_value")
                    new_val = change.get("new_value")

                    # Check money tolerance
                    if self._is_money_path(path) and self._is_numeric(old_val) and self._is_numeric(new_val):
                        diff_amount = abs(float(new_val) - float(old_val))
                        if diff_amount <= self.config.money_tolerance:
                            ignored.append(f"{readable_path} (within tolerance: {diff_amount:.4f})")
                            continue
                        differences.append(Difference(
                            diff_type=DiffType.MONEY_TOLERANCE_EXCEEDED,
                            path=readable_path,
                            golden_value=old_val,
                            candidate_value=new_val,
                            message=f"Money value changed by ${diff_amount:.2f} (tolerance: ${self.config.money_tolerance})",
                        ))
                    # Check float tolerance for non-money numerics
                    elif self._is_numeric(old_val) and self._is_numeric(new_val):
                        diff_amount = abs(float(new_val) - float(old_val))
                        if diff_amount <= self.config.float_tolerance:
                            ignored.append(f"{readable_path} (within float tolerance)")
                            continue
                        differences.append(Difference(
                            diff_type=DiffType.VALUE_CHANGED,
                            path=readable_path,
                            golden_value=old_val,
                            candidate_value=new_val,
                            message=f"Value changed from {old_val} to {new_val}",
                        ))
                    else:
                        differences.append(Difference(
                            diff_type=DiffType.VALUE_CHANGED,
                            path=readable_path,
                            golden_value=old_val,
                            candidate_value=new_val,
                            message=f"Value changed from {old_val!r} to {new_val!r}",
                        ))

            elif change_type == "type_changes":
                for path, change in changes.items():
                    readable_path = self._extract_path_key(path)
                    if self._should_ignore(path):
                        ignored.append(readable_path)
                        continue

                    differences.append(Difference(
                        diff_type=DiffType.TYPE_CHANGED,
                        path=readable_path,
                        golden_value=f"{type(change.get('old_value')).__name__}: {change.get('old_value')}",
                        candidate_value=f"{type(change.get('new_value')).__name__}: {change.get('new_value')}",
                        message=f"Type changed at {readable_path}",
                    ))

            elif change_type == "dictionary_item_added":
                for path in changes:
                    readable_path = self._extract_path_key(path)
                    if self._should_ignore(path):
                        ignored.append(readable_path)
                        continue

                    differences.append(Difference(
                        diff_type=DiffType.ITEM_ADDED,
                        path=readable_path,
                        golden_value=None,
                        candidate_value=changes[path],
                        message=f"New field added: {readable_path}",
                    ))

            elif change_type == "dictionary_item_removed":
                for path in changes:
                    readable_path = self._extract_path_key(path)
                    if self._should_ignore(path):
                        ignored.append(readable_path)
                        continue

                    differences.append(Difference(
                        diff_type=DiffType.ITEM_REMOVED,
                        path=readable_path,
                        golden_value=changes[path],
                        candidate_value=None,
                        message=f"Field removed: {readable_path}",
                    ))

            elif change_type in ("iterable_item_added", "iterable_item_removed"):
                for path, value in changes.items():
                    readable_path = self._extract_path_key(path)
                    if self._should_ignore(path):
                        ignored.append(readable_path)
                        continue

                    diff_type = DiffType.ITEM_ADDED if "added" in change_type else DiffType.ITEM_REMOVED
                    differences.append(Difference(
                        diff_type=diff_type,
                        path=readable_path,
                        golden_value=None if "added" in change_type else value,
                        candidate_value=value if "added" in change_type else None,
                        message=f"Array item {'added' if 'added' in change_type else 'removed'}: {readable_path}",
                    ))

        return DiffResult(
            fixture_id=fixture_id,
            endpoint=endpoint,
            passed=len(differences) == 0,
            differences=differences,
            ignored_paths=ignored,
        )

    def _is_numeric(self, value: Any) -> bool:
        """Check if a value is numeric."""
        return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class SimulationReport:
    """
    Complete simulation report for a PR validation run.

    Answers three questions:
    1. Did this PR introduce a logic regression?
    2. Where?
    3. Why does it matter?
    """
    run_id: str
    candidate_image: str
    total_fixtures: int
    passed_fixtures: int
    failed_fixtures: int
    results: List[DiffResult] = field(default_factory=list)
    stub_misses: List[Dict[str, Any]] = field(default_factory=list)
    blocked_writes: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.failed_fixtures == 0 and len(self.stub_misses) == 0

    def to_markdown(self) -> str:
        """Generate markdown report."""
        lines = [
            "# Simulation Report",
            "",
            f"**Run ID:** {self.run_id}",
            f"**Candidate:** {self.candidate_image}",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Fixtures | {self.total_fixtures} |",
            f"| Passed | {self.passed_fixtures} |",
            f"| Failed | {self.failed_fixtures} |",
            f"| Stub Misses | {len(self.stub_misses)} |",
            f"| Blocked Writes | {len(self.blocked_writes)} |",
            "",
            f"## Overall Status: {'✅ PASSED' if self.passed else '❌ FAILED'}",
            "",
        ]

        if self.failed_fixtures > 0:
            lines.append("## Regressions Detected")
            lines.append("")

            # Group by endpoint
            by_endpoint: Dict[str, List[DiffResult]] = {}
            for result in self.results:
                if result.has_regressions:
                    by_endpoint.setdefault(result.endpoint, []).append(result)

            for endpoint, results in by_endpoint.items():
                lines.append(f"### {endpoint}")
                lines.append("")

                for result in results:
                    lines.append(f"#### Fixture: {result.fixture_id}")
                    lines.append("")
                    lines.append("| Path | Golden | Candidate | Issue |")
                    lines.append("|------|--------|-----------|-------|")

                    for diff in result.differences:
                        golden_str = str(diff.golden_value)[:30]
                        candidate_str = str(diff.candidate_value)[:30]
                        lines.append(f"| `{diff.path}` | {golden_str} | {candidate_str} | {diff.message} |")

                    lines.append("")

        if self.stub_misses:
            lines.append("## Stub Misses")
            lines.append("")
            lines.append("The following dependency calls did not have recorded stubs:")
            lines.append("")
            for miss in self.stub_misses:
                lines.append(f"- **{miss.get('type', 'unknown')}**: {miss.get('details', 'N/A')}")
            lines.append("")

        if self.blocked_writes:
            lines.append("## Blocked Writes")
            lines.append("")
            lines.append("The following write operations were blocked in replay mode:")
            lines.append("")
            for write in self.blocked_writes:
                lines.append(f"- {write.get('sql', write.get('details', 'N/A'))[:100]}")
            lines.append("")

        if self.passed:
            lines.append("---")
            lines.append("")
            lines.append("✅ No regressions detected. All fixtures matched golden outputs.")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "candidate_image": self.candidate_image,
            "passed": self.passed,
            "total_fixtures": self.total_fixtures,
            "passed_fixtures": self.passed_fixtures,
            "failed_fixtures": self.failed_fixtures,
            "results": [r.to_dict() for r in self.results],
            "stub_misses": self.stub_misses,
            "blocked_writes": self.blocked_writes,
        }


def compare_responses(
    golden: Dict[str, Any],
    candidate: Dict[str, Any],
    fixture_id: str = "unknown",
    endpoint: str = "unknown",
    config: Optional[DiffConfig] = None,
) -> DiffResult:
    """
    Convenience function to compare two responses.

    Args:
        golden: Golden/expected response
        candidate: Candidate response
        fixture_id: ID for reporting
        endpoint: Endpoint name for reporting
        config: Optional diff configuration

    Returns:
        DiffResult with comparison details
    """
    engine = DiffEngine(config)
    return engine.compare(fixture_id, endpoint, golden, candidate)
