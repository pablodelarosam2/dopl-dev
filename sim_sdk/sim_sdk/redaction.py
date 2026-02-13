"""
PII redaction using JSONPath patterns.

Removes or masks sensitive fields from data before capture/comparison.

Note: JSONPath support requires the 'jsonpath-ng' package (optional dependency).
      If not installed, only simple dict key-based redaction is available.
"""

import copy
import re
from typing import Any, Dict, List, Optional, Union

# Optional dependency - jsonpath_ng
try:
    from jsonpath_ng import parse as jsonpath_parse
    from jsonpath_ng.exceptions import JsonPathParserError
    HAS_JSONPATH = True
except ImportError:
    HAS_JSONPATH = False
    JsonPathParserError = Exception


# Default paths to redact (common PII fields)
DEFAULT_REDACT_PATHS = [
    "$.*.email",
    "$.*.password",
    "$.*.ssn",
    "$.*.social_security_number",
    "$.*.card_number",
    "$.*.credit_card",
    "$.*.cvv",
    "$.*.cvc",
    "$.*.secret",
    "$.*.api_key",
    "$.*.token",
    "$.*.auth",
    "$.*.authorization",
]

# Placeholder for redacted values
REDACTED_PLACEHOLDER = "[REDACTED]"


def redact(
    data: Union[Dict, List, Any],
    paths: Optional[List[str]] = None,
    placeholder: str = REDACTED_PLACEHOLDER,
    in_place: bool = False,
) -> Union[Dict, List, Any]:
    """
    Redact sensitive fields from data using JSONPath patterns.
    
    Note: Requires 'jsonpath-ng' package for full JSONPath support.
          Falls back to simple key-based redaction if not available.

    Args:
        data: Data structure to redact
        paths: List of JSONPath patterns to redact. Defaults to DEFAULT_REDACT_PATHS
        placeholder: Value to replace redacted fields with
        in_place: If True, modify data in place. Otherwise, work on a deep copy

    Returns:
        Data with sensitive fields redacted

    Examples:
        >>> redact({"user": {"email": "test@example.com"}}, ["$.*.email"])
        {"user": {"email": "[REDACTED]"}}
    """
    if data is None:
        return None

    if not isinstance(data, (dict, list)):
        return data

    if paths is None:
        paths = DEFAULT_REDACT_PATHS

    if not paths:
        return data

    # Work on a copy unless in_place is True
    result = data if in_place else copy.deepcopy(data)

    if not HAS_JSONPATH:
        # Fallback to simple key-based redaction if jsonpath-ng not available
        return _simple_redact(result, paths, placeholder)

    for path in paths:
        try:
            jsonpath_expr = jsonpath_parse(path)
            matches = jsonpath_expr.find(result)

            for match in matches:
                _set_value_at_path(result, match.full_path, placeholder)
        except JsonPathParserError:
            # Skip invalid patterns
            continue
        except Exception:
            # Skip on any other error to avoid breaking capture
            continue

    return result


def _simple_redact(data: Any, paths: List[str], placeholder: str) -> Any:
    """
    Simple key-based redaction when jsonpath-ng is not available.
    
    Only handles simple patterns like "$.password" or "$.*.email"
    """
    if not isinstance(data, dict):
        return data
    
    # Extract simple key names from paths
    keys_to_redact = set()
    for path in paths:
        # Extract last part after $. or $.*. 
        if '.*.' in path:
            key = path.split('*.')[-1]
            keys_to_redact.add(key)
        elif path.startswith('$.'):
            key = path[2:].split('.')[0]
            keys_to_redact.add(key)
    
    # Recursively redact matching keys
    def redact_dict(d):
        if isinstance(d, dict):
            return {k: placeholder if k in keys_to_redact else redact_dict(v) for k, v in d.items()}
        elif isinstance(d, list):
            return [redact_dict(item) for item in d]
        else:
            return d
    
    return redact_dict(data)


def _set_value_at_path(data: Any, path: Any, value: Any) -> None:
    """
    Set a value at a JSONPath location.

    This is a helper to update the actual data structure based on the match path.
    """
    # Build the path segments
    path_str = str(path)

    # Parse path segments (handles both dict keys and array indices)
    segments = _parse_path_segments(path_str)

    if not segments:
        return

    # Navigate to parent and set value
    current = data
    for segment in segments[:-1]:
        if isinstance(segment, int):
            current = current[segment]
        else:
            current = current[segment]

    last_segment = segments[-1]
    if isinstance(last_segment, int):
        current[last_segment] = value
    else:
        current[last_segment] = value


def _parse_path_segments(path_str: str) -> List[Union[str, int]]:
    """
    Parse a JSONPath string into segments.

    Args:
        path_str: JSONPath string like "user.email" or "items[0].name"

    Returns:
        List of path segments (strings for dict keys, ints for array indices)
    """
    segments = []

    # Remove leading $ if present
    if path_str.startswith("$"):
        path_str = path_str[1:]

    # Split on dots and brackets
    pattern = r'\.?([^\.\[\]]+)|\[(\d+)\]'
    matches = re.findall(pattern, path_str)

    for match in matches:
        if match[0]:  # Dict key
            segments.append(match[0])
        elif match[1]:  # Array index
            segments.append(int(match[1]))

    return segments


def create_redactor(
    paths: List[str],
    placeholder: str = REDACTED_PLACEHOLDER,
) -> callable:
    """
    Create a reusable redaction function with preset paths.

    Args:
        paths: List of JSONPath patterns to redact
        placeholder: Value to replace redacted fields with

    Returns:
        A function that takes data and returns redacted data

    Example:
        >>> my_redactor = create_redactor(["$.email", "$.ssn"])
        >>> my_redactor({"email": "test@example.com", "name": "John"})
        {"email": "[REDACTED]", "name": "John"}
    """
    def redactor(data: Union[Dict, List, Any]) -> Union[Dict, List, Any]:
        return redact(data, paths=paths, placeholder=placeholder)

    return redactor


def detect_sensitive_keys(data: Union[Dict, List, Any]) -> List[str]:
    """
    Scan data for potentially sensitive keys based on common patterns.

    Args:
        data: Data structure to scan

    Returns:
        List of JSONPaths to potentially sensitive fields
    """
    sensitive_patterns = [
        r"(?i)email",
        r"(?i)password",
        r"(?i)passwd",
        r"(?i)ssn",
        r"(?i)social.?security",
        r"(?i)card.?number",
        r"(?i)credit.?card",
        r"(?i)cvv",
        r"(?i)cvc",
        r"(?i)secret",
        r"(?i)api.?key",
        r"(?i)token",
        r"(?i)auth",
        r"(?i)bearer",
        r"(?i)phone",
        r"(?i)address",
        r"(?i)dob",
        r"(?i)birth.?date",
    ]

    found_paths = []
    _scan_for_sensitive(data, "$", sensitive_patterns, found_paths)
    return found_paths


def _scan_for_sensitive(
    data: Any,
    current_path: str,
    patterns: List[str],
    found: List[str],
) -> None:
    """Recursively scan for sensitive keys."""
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{current_path}.{key}"

            # Check if key matches any sensitive pattern
            for pattern in patterns:
                if re.search(pattern, key):
                    found.append(path)
                    break

            # Recurse into value
            _scan_for_sensitive(value, path, patterns, found)

    elif isinstance(data, list):
        for i, item in enumerate(data):
            path = f"{current_path}[{i}]"
            _scan_for_sensitive(item, path, patterns, found)
