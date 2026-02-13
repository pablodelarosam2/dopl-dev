"""
PII redaction and pseudonymization using JSONPath patterns.

Provides two strategies for handling sensitive data:

1. **Redaction**: Replace sensitive values with a placeholder (e.g., "[REDACTED]")
   - Simple and secure
   - ⚠️ Breaks determinism if redacted values affect control flow

2. **Pseudonymization**: Hash sensitive values to consistent pseudonyms
   - Preserves equality relationships (same input → same pseudonym)
   - Maintains deterministic behavior for simulations
   - Still protects actual PII values

Note: JSONPath support requires the 'jsonpath-ng' package (optional dependency).
      If not installed, only simple dict key-based redaction is available.
"""

import copy
import hashlib
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

# Default salt for pseudonymization (can be overridden for environment-specific salts)
DEFAULT_PSEUDONYM_SALT = "sim_sdk_default_salt_v1"


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
        return _simple_transform(result, paths, lambda v: placeholder)

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


def _simple_transform(data: Any, paths: List[str], transform_fn: callable) -> Any:
    """
    Simple key-based transformation when jsonpath-ng is not available.
    
    Only handles simple patterns like "$.password" or "$.*.email"
    
    Args:
        data: Data to transform
        paths: List of JSONPath patterns
        transform_fn: Function to apply to matching values
    
    Returns:
        Transformed data
    """
    if not isinstance(data, dict):
        return data
    
    # Extract simple key names from paths
    keys_to_transform = set()
    for path in paths:
        # Extract last part after $. or $.*. 
        if '.*.' in path:
            key = path.split('*.')[-1]
            keys_to_transform.add(key)
        elif path.startswith('$.'):
            key = path[2:].split('.')[0]
            keys_to_transform.add(key)
    
    # Recursively transform matching keys
    def transform_dict(d):
        if isinstance(d, dict):
            return {k: transform_fn(v) if k in keys_to_transform else transform_dict(v) 
                    for k, v in d.items()}
        elif isinstance(d, list):
            return [transform_dict(item) for item in d]
        else:
            return d
    
    return transform_dict(data)


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


def pseudonymize(
    data: Union[Dict, List, Any],
    paths: Optional[List[str]] = None,
    salt: str = DEFAULT_PSEUDONYM_SALT,
    length: int = 16,
    in_place: bool = False,
) -> Union[Dict, List, Any]:
    """
    Pseudonymize sensitive fields using deterministic hashing.
    
    Unlike redaction, pseudonymization preserves equality relationships:
    - Same input value → same pseudonym
    - Different input values → different pseudonyms
    
    This is critical for deterministic simulations where control flow
    depends on the values of sensitive fields (e.g., email-based routing).
    
    Args:
        data: Data structure to pseudonymize
        paths: List of JSONPath patterns to pseudonymize. Defaults to DEFAULT_REDACT_PATHS
        salt: Salt for hashing (use environment-specific salt for isolation)
        length: Length of pseudonym hash (default: 16 characters)
        in_place: If True, modify data in place. Otherwise, work on a deep copy
    
    Returns:
        Data with sensitive fields pseudonymized
    
    Examples:
        >>> data = {"user": {"email": "alice@example.com", "name": "Alice"}}
        >>> result = pseudonymize(data, ["$.*.email"])
        >>> result
        {"user": {"email": "a3f8c9d2e1b4f7a2", "name": "Alice"}}
        
        >>> # Same email always produces same pseudonym
        >>> data2 = {"admin": {"email": "alice@example.com"}}
        >>> result2 = pseudonymize(data2, ["$.*.email"])
        >>> result2["admin"]["email"] == result["user"]["email"]
        True
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
    
    # Create pseudonymizer function
    def make_pseudonym(value: Any) -> str:
        """Generate deterministic pseudonym for a value."""
        if value is None:
            return None
        # Convert value to string for hashing
        value_str = str(value)
        # Hash with salt
        hash_input = f"{salt}:{value_str}".encode('utf-8')
        full_hash = hashlib.sha256(hash_input).hexdigest()
        return full_hash[:length]
    
    if not HAS_JSONPATH:
        # Fallback to simple key-based pseudonymization
        return _simple_transform(result, paths, make_pseudonym)
    
    for path in paths:
        try:
            jsonpath_expr = jsonpath_parse(path)
            matches = jsonpath_expr.find(result)
            
            for match in matches:
                original_value = match.value
                pseudonym = make_pseudonym(original_value)
                _set_value_at_path(result, match.full_path, pseudonym)
        except JsonPathParserError:
            # Skip invalid patterns
            continue
        except Exception:
            # Skip on any other error to avoid breaking capture
            continue
    
    return result


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


def create_pseudonymizer(
    paths: List[str],
    salt: str = DEFAULT_PSEUDONYM_SALT,
    length: int = 16,
) -> callable:
    """
    Create a reusable pseudonymization function with preset paths.
    
    Args:
        paths: List of JSONPath patterns to pseudonymize
        salt: Salt for hashing
        length: Length of pseudonym hash
    
    Returns:
        A function that takes data and returns pseudonymized data
    
    Example:
        >>> my_pseudonymizer = create_pseudonymizer(["$.email", "$.ssn"])
        >>> my_pseudonymizer({"email": "test@example.com", "name": "John"})
        {"email": "a3f8c9d2e1b4f7a2", "name": "John"}
    """
    def pseudonymizer(data: Union[Dict, List, Any]) -> Union[Dict, List, Any]:
        return pseudonymize(data, paths=paths, salt=salt, length=length)
    
    return pseudonymizer


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
