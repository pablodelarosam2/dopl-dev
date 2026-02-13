"""
JSON canonicalization and fingerprinting utilities.

Provides deterministic JSON serialization and content-based hashing
for generating stable fixture identifiers.
"""

import json
import hashlib
from typing import Any, Dict, List, Optional


def canonicalize_json(obj: Any) -> str:
    """
    Serialize an object to canonical JSON format.
    
    Ensures deterministic output by:
    - Sorting dictionary keys
    - Using consistent formatting (no whitespace)
    - Handling special types consistently
    
    Args:
        obj: Python object to canonicalize
        
    Returns:
        Canonical JSON string
    """
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(',', ':'),
        default=_default_serializer
    )


def fingerprint(obj: Any) -> str:
    """
    Generate a content-based fingerprint (hash) of an object.
    
    Uses SHA-256 hash of the canonical JSON representation.
    
    Args:
        obj: Python object to fingerprint
        
    Returns:
        Hexadecimal hash string (64 characters)
    """
    canonical = canonicalize_json(obj)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def fingerprint_short(obj: Any, length: int = 16) -> str:
    """
    Generate a short content-based fingerprint.
    
    Args:
        obj: Python object to fingerprint
        length: Number of characters to return (default: 16)
        
    Returns:
        Truncated hexadecimal hash string
    """
    return fingerprint(obj)[:length]


def _default_serializer(obj: Any) -> Any:
    """
    Default serializer for objects that aren't JSON-serializable.
    
    Args:
        obj: Object to serialize
        
    Returns:
        Serializable representation
        
    Raises:
        TypeError: If object cannot be serialized
    """
    # Handle common types
    if hasattr(obj, 'isoformat'):
        # datetime objects
        return obj.isoformat()
    elif hasattr(obj, '__dict__'):
        # Objects with __dict__
        return obj.__dict__
    elif hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes)):
        # Iterables (excluding strings)
        return list(obj)
    else:
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
