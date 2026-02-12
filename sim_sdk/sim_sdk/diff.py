"""
Minimal V0 Diff Engine for comparing candidate responses against golden outputs.

Features:
- Status code comparison
- JSON deep-diff
- Money tolerance (configurable absolute threshold)
- Ignore paths for non-deterministic fields (request_id, timestamp, etc.)
- HTML diff visualization with color highlighting
- Optional PNG image export

The diff engine takes two response sets as input and does not know their source.
Whether they come from a golden snapshot file or a live baseline container is transparent.
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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

    def to_html(self, include_json_diff: bool = True, golden_body: Optional[Dict] = None, candidate_body: Optional[Dict] = None) -> str:
        """
        Generate HTML visualization of the diff.

        Args:
            include_json_diff: Include side-by-side JSON comparison
            golden_body: Original golden response body for JSON diff
            candidate_body: Original candidate response body for JSON diff

        Returns:
            HTML string with styled diff visualization
        """
        status_class = "passed" if self.passed else "failed"
        status_icon = "✅" if self.passed else "❌"

        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Diff Result: {self.fixture_id}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace; background: #1a1a2e; color: #eee; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
        .header h1 {{ font-size: 24px; margin-bottom: 10px; }}
        .header .meta {{ font-size: 14px; opacity: 0.9; }}
        .status {{ display: inline-block; padding: 5px 15px; border-radius: 20px; font-weight: bold; margin-top: 10px; }}
        .status.passed {{ background: #10b981; color: white; }}
        .status.failed {{ background: #ef4444; color: white; }}
        .diff-table {{ width: 100%; border-collapse: collapse; background: #16213e; border-radius: 10px; overflow: hidden; margin-bottom: 20px; }}
        .diff-table th {{ background: #0f3460; padding: 15px; text-align: left; font-weight: 600; }}
        .diff-table td {{ padding: 12px 15px; border-bottom: 1px solid #1a1a2e; }}
        .diff-table tr:last-child td {{ border-bottom: none; }}
        .path {{ font-family: monospace; color: #60a5fa; }}
        .golden {{ color: #34d399; background: rgba(52, 211, 153, 0.1); padding: 4px 8px; border-radius: 4px; font-family: monospace; }}
        .candidate {{ color: #f87171; background: rgba(248, 113, 113, 0.1); padding: 4px 8px; border-radius: 4px; font-family: monospace; }}
        .message {{ color: #fbbf24; font-size: 13px; }}
        .diff-type {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; text-transform: uppercase; }}
        .diff-type.value_changed, .diff-type.money_tolerance_exceeded {{ background: #f59e0b; color: #1a1a2e; }}
        .diff-type.item_added {{ background: #10b981; color: white; }}
        .diff-type.item_removed {{ background: #ef4444; color: white; }}
        .diff-type.status_code {{ background: #8b5cf6; color: white; }}
        .diff-type.type_changed {{ background: #ec4899; color: white; }}
        .json-diff {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px; }}
        .json-panel {{ background: #16213e; border-radius: 10px; overflow: hidden; }}
        .json-panel h3 {{ background: #0f3460; padding: 10px 15px; font-size: 14px; }}
        .json-panel.golden h3 {{ border-left: 4px solid #34d399; }}
        .json-panel.candidate h3 {{ border-left: 4px solid #f87171; }}
        .json-content {{ padding: 15px; font-family: monospace; font-size: 13px; white-space: pre-wrap; max-height: 400px; overflow-y: auto; }}
        .json-content .changed {{ background: rgba(251, 191, 36, 0.3); padding: 2px 4px; border-radius: 2px; }}
        .ignored {{ margin-top: 20px; padding: 15px; background: #16213e; border-radius: 10px; }}
        .ignored h3 {{ margin-bottom: 10px; color: #9ca3af; }}
        .ignored ul {{ list-style: none; }}
        .ignored li {{ padding: 5px 0; color: #6b7280; font-size: 13px; }}
        .arrow {{ color: #9ca3af; margin: 0 10px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{status_icon} Diff Result</h1>
            <div class="meta">
                <strong>Fixture:</strong> {self.fixture_id} &nbsp;|&nbsp;
                <strong>Endpoint:</strong> {self.endpoint}
            </div>
            <div class="status {status_class}">
                {"PASSED" if self.passed else f"FAILED - {len(self.differences)} regression(s) detected"}
            </div>
        </div>
'''

        if self.differences:
            html += '''
        <table class="diff-table">
            <thead>
                <tr>
                    <th style="width: 15%">Type</th>
                    <th style="width: 20%">Path</th>
                    <th style="width: 25%">Golden (Expected)</th>
                    <th style="width: 25%">Candidate (Actual)</th>
                    <th style="width: 15%">Issue</th>
                </tr>
            </thead>
            <tbody>
'''
            for diff in self.differences:
                diff_type_class = diff.diff_type.value
                golden_val = json.dumps(diff.golden_value) if not isinstance(diff.golden_value, str) else diff.golden_value
                candidate_val = json.dumps(diff.candidate_value) if not isinstance(diff.candidate_value, str) else diff.candidate_value

                html += f'''
                <tr>
                    <td><span class="diff-type {diff_type_class}">{diff.diff_type.value.replace('_', ' ')}</span></td>
                    <td class="path">{diff.path}</td>
                    <td><span class="golden">{golden_val}</span></td>
                    <td><span class="candidate">{candidate_val}</span></td>
                    <td class="message">{diff.message}</td>
                </tr>
'''
            html += '''
            </tbody>
        </table>
'''

        # Add JSON side-by-side diff if bodies provided
        if include_json_diff and golden_body and candidate_body:
            golden_json = json.dumps(golden_body, indent=2, default=str)
            candidate_json = json.dumps(candidate_body, indent=2, default=str)

            # Highlight changed paths in the JSON
            changed_paths = {d.path for d in self.differences}

            html += f'''
        <div class="json-diff">
            <div class="json-panel golden">
                <h3>Golden (Expected)</h3>
                <div class="json-content">{self._highlight_json(golden_json, changed_paths, 'golden')}</div>
            </div>
            <div class="json-panel candidate">
                <h3>Candidate (Actual)</h3>
                <div class="json-content">{self._highlight_json(candidate_json, changed_paths, 'candidate')}</div>
            </div>
        </div>
'''

        if self.ignored_paths:
            html += '''
        <div class="ignored">
            <h3>Ignored Paths</h3>
            <ul>
'''
            for path in self.ignored_paths:
                html += f'                <li>• {path}</li>\n'
            html += '''
            </ul>
        </div>
'''

        html += '''
    </div>
</body>
</html>'''
        return html

    def _highlight_json(self, json_str: str, changed_paths: Set[str], side: str) -> str:
        """Highlight changed values in JSON string."""
        import html as html_module
        result = html_module.escape(json_str)

        # Simple highlighting - mark lines containing changed paths
        for path in changed_paths:
            # Get the last key in the path
            key = path.split('.')[-1] if '.' in path else path
            # Highlight the key and its value
            pattern = rf'("{re.escape(key)}":\s*[^\n,}}]+)'
            result = re.sub(pattern, r'<span class="changed">\1</span>', result)

        return result

    def save_html(self, filepath: Union[str, Path], **kwargs) -> Path:
        """
        Save diff visualization as HTML file.

        Args:
            filepath: Output file path
            **kwargs: Additional arguments for to_html()

        Returns:
            Path to saved file
        """
        filepath = Path(filepath)
        filepath.write_text(self.to_html(**kwargs), encoding='utf-8')
        return filepath

    def save_image(self, filepath: Union[str, Path], **kwargs) -> Optional[Path]:
        """
        Save diff visualization as PNG image.

        Requires: pip install imgkit
        Also requires wkhtmltopdf/wkhtmltoimage installed on system.

        Args:
            filepath: Output file path (.png)
            **kwargs: Additional arguments for to_html()

        Returns:
            Path to saved image, or None if imgkit not available
        """
        try:
            import imgkit
        except ImportError:
            print("Warning: imgkit not installed. Run: pip install imgkit")
            print("Also install wkhtmltopdf: https://wkhtmltopdf.org/downloads.html")
            return None

        filepath = Path(filepath)
        html = self.to_html(**kwargs)

        try:
            imgkit.from_string(html, str(filepath), options={
                'width': '1200',
                'quality': '100',
                'enable-local-file-access': None,
            })
            return filepath
        except Exception as e:
            print(f"Warning: Failed to generate image: {e}")
            return None


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

    def to_html(self) -> str:
        """Generate HTML visualization of the full simulation report."""
        status_class = "passed" if self.passed else "failed"
        status_icon = "✅" if self.passed else "❌"

        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Simulation Report: {self.run_id}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace; background: #1a1a2e; color: #eee; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 10px; margin-bottom: 20px; text-align: center; }}
        .header h1 {{ font-size: 28px; margin-bottom: 10px; }}
        .header .meta {{ font-size: 14px; opacity: 0.9; margin-bottom: 15px; }}
        .status-badge {{ display: inline-block; padding: 10px 30px; border-radius: 30px; font-weight: bold; font-size: 18px; }}
        .status-badge.passed {{ background: #10b981; color: white; }}
        .status-badge.failed {{ background: #ef4444; color: white; }}
        .summary {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 15px; margin-bottom: 20px; }}
        .stat {{ background: #16213e; padding: 20px; border-radius: 10px; text-align: center; }}
        .stat .value {{ font-size: 32px; font-weight: bold; color: #60a5fa; }}
        .stat .label {{ font-size: 12px; color: #9ca3af; margin-top: 5px; text-transform: uppercase; }}
        .stat.failed .value {{ color: #ef4444; }}
        .stat.passed .value {{ color: #10b981; }}
        .section {{ background: #16213e; border-radius: 10px; margin-bottom: 20px; overflow: hidden; }}
        .section h2 {{ background: #0f3460; padding: 15px 20px; font-size: 16px; border-left: 4px solid #ef4444; }}
        .section.success h2 {{ border-left-color: #10b981; }}
        .diff-table {{ width: 100%; border-collapse: collapse; }}
        .diff-table th {{ background: #0f3460; padding: 12px 15px; text-align: left; font-weight: 600; font-size: 13px; }}
        .diff-table td {{ padding: 10px 15px; border-bottom: 1px solid #1a1a2e; font-size: 13px; }}
        .path {{ font-family: monospace; color: #60a5fa; }}
        .golden {{ color: #34d399; }}
        .candidate {{ color: #f87171; }}
        .endpoint {{ background: #8b5cf6; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
        .fixture-id {{ color: #9ca3af; font-size: 12px; }}
        .no-issues {{ padding: 30px; text-align: center; color: #10b981; font-size: 18px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{status_icon} Simulation Report</h1>
            <div class="meta">
                <strong>Run ID:</strong> {self.run_id} &nbsp;|&nbsp;
                <strong>Candidate:</strong> {self.candidate_image}
            </div>
            <div class="status-badge {status_class}">
                {"ALL TESTS PASSED" if self.passed else "REGRESSIONS DETECTED"}
            </div>
        </div>

        <div class="summary">
            <div class="stat">
                <div class="value">{self.total_fixtures}</div>
                <div class="label">Total Fixtures</div>
            </div>
            <div class="stat passed">
                <div class="value">{self.passed_fixtures}</div>
                <div class="label">Passed</div>
            </div>
            <div class="stat {"failed" if self.failed_fixtures > 0 else ""}">
                <div class="value">{self.failed_fixtures}</div>
                <div class="label">Failed</div>
            </div>
            <div class="stat {"failed" if self.stub_misses else ""}">
                <div class="value">{len(self.stub_misses)}</div>
                <div class="label">Stub Misses</div>
            </div>
            <div class="stat">
                <div class="value">{len(self.blocked_writes)}</div>
                <div class="label">Blocked Writes</div>
            </div>
        </div>
'''

        # Failed fixtures section
        failed_results = [r for r in self.results if r.has_regressions]
        if failed_results:
            html += '''
        <div class="section">
            <h2>❌ Regressions Detected</h2>
            <table class="diff-table">
                <thead>
                    <tr>
                        <th>Endpoint</th>
                        <th>Fixture</th>
                        <th>Path</th>
                        <th>Golden</th>
                        <th>Candidate</th>
                    </tr>
                </thead>
                <tbody>
'''
            for result in failed_results:
                for diff in result.differences:
                    golden_val = str(diff.golden_value)[:25]
                    candidate_val = str(diff.candidate_value)[:25]
                    html += f'''
                    <tr>
                        <td><span class="endpoint">{result.endpoint}</span></td>
                        <td class="fixture-id">{result.fixture_id}</td>
                        <td class="path">{diff.path}</td>
                        <td class="golden">{golden_val}</td>
                        <td class="candidate">{candidate_val}</td>
                    </tr>
'''
            html += '''
                </tbody>
            </table>
        </div>
'''
        else:
            html += '''
        <div class="section success">
            <h2>✅ All Tests Passed</h2>
            <div class="no-issues">No regressions detected. All fixtures matched golden outputs.</div>
        </div>
'''

        html += '''
    </div>
</body>
</html>'''
        return html

    def save_html(self, filepath: Union[str, Path]) -> Path:
        """Save report as HTML file."""
        filepath = Path(filepath)
        filepath.write_text(self.to_html(), encoding='utf-8')
        return filepath

    def save_image(self, filepath: Union[str, Path]) -> Optional[Path]:
        """Save report as PNG image (requires imgkit + wkhtmltoimage)."""
        try:
            import imgkit
        except ImportError:
            print("Warning: imgkit not installed. Run: pip install imgkit")
            return None

        filepath = Path(filepath)
        try:
            imgkit.from_string(self.to_html(), str(filepath), options={
                'width': '1200',
                'quality': '100',
            })
            return filepath
        except Exception as e:
            print(f"Warning: Failed to generate image: {e}")
            return None


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
