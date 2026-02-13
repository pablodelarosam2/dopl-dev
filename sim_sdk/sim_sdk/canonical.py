"""
JSON canonicalization and fingerprinting utilities.

Provides deterministic JSON serialization and content-based hashing
for generating stable fixture identifiers.

Supports serialization of:
- Standard JSON types
- Datetime objects
- Protobuf messages (optional, requires protobuf package)
- Avro records (optional, requires fastavro package)
"""

import json
import hashlib
from typing import Any, Dict, List, Optional

# Optional dependencies for structured data formats
try:
    from google.protobuf.message import Message as ProtobufMessage
    from google.protobuf.json_format import MessageToDict
    HAS_PROTOBUF = True
except ImportError:
    HAS_PROTOBUF = False
    ProtobufMessage = None

try:
    import fastavro
    HAS_AVRO = True
except ImportError:
    HAS_AVRO = False

try:
    import sqlparse
    HAS_SQLPARSE = True
except ImportError:
    HAS_SQLPARSE = False


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


def normalize_sql(query: str, strip_comments: bool = True) -> str:
    """
    Normalize SQL query for deterministic fingerprinting.
    
    Ensures that semantically identical queries produce the same fingerprint
    regardless of formatting differences:
    - Removes extra whitespace
    - Normalizes keyword case (uppercase)
    - Removes comments (optional)
    - Strips leading/trailing whitespace
    
    Args:
        query: SQL query string to normalize
        strip_comments: Whether to remove SQL comments (default: True)
        
    Returns:
        Normalized SQL query string
        
    Examples:
        >>> normalize_sql("SELECT * FROM users WHERE id = 1")
        'SELECT * FROM users WHERE id = 1'
        
        >>> normalize_sql("select  *  from\\n  users\\n  where id=1")
        'SELECT * FROM users WHERE id = 1'
    """
    if not query or not isinstance(query, str):
        return query
    
    if HAS_SQLPARSE:
        # Use sqlparse for robust SQL normalization
        return _normalize_sql_with_parser(query, strip_comments)
    else:
        # Fallback to basic normalization
        return _normalize_sql_basic(query, strip_comments)


def _normalize_sql_with_parser(query: str, strip_comments: bool) -> str:
    """
    Normalize SQL using sqlparse library.
    
    Args:
        query: SQL query string
        strip_comments: Whether to remove comments
        
    Returns:
        Normalized SQL string
    """
    # Parse and format the SQL
    parsed = sqlparse.parse(query)
    
    if not parsed:
        return query.strip()
    
    # Format with consistent options
    formatted = sqlparse.format(
        query,
        keyword_case='upper',
        identifier_case='lower',
        strip_comments=strip_comments,
        reindent=False,
        use_space_around_operators=True,
    )
    
    # Collapse multiple whitespace into single space
    import re
    normalized = re.sub(r'\s+', ' ', formatted)
    
    return normalized.strip()


def _normalize_sql_basic(query: str, strip_comments: bool) -> str:
    """
    Basic SQL normalization without sqlparse.
    
    Less robust but handles common cases:
    - Collapses whitespace
    - Normalizes spacing around operators
    - Uppercases SQL keywords
    - Optionally removes comments
    
    Args:
        query: SQL query string
        strip_comments: Whether to remove comments
        
    Returns:
        Normalized SQL string
    """
    import re
    
    # Remove SQL comments if requested
    if strip_comments:
        # Remove single-line comments (-- ...)
        query = re.sub(r'--[^\n]*', '', query)
        # Remove multi-line comments (/* ... */)
        query = re.sub(r'/\*.*?\*/', '', query, flags=re.DOTALL)
    
    # Collapse multiple whitespace (including newlines) into single space
    query = re.sub(r'\s+', ' ', query)
    
    # Normalize spacing around common operators
    # Add space around =, <, >, !=, <=, >=
    operators = ['=', '!=', '<=', '>=', '<', '>']
    for op in operators:
        # Remove existing spaces around operator
        query = re.sub(r'\s*' + re.escape(op) + r'\s*', op, query)
        # Add consistent spacing
        query = query.replace(op, f' {op} ')
    
    # Clean up any double spaces created
    query = re.sub(r'\s+', ' ', query)
    
    # Uppercase common SQL keywords
    keywords = [
        'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'NOT', 'IN', 'LIKE',
        'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE',
        'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON', 'AS',
        'ORDER', 'BY', 'GROUP', 'HAVING', 'LIMIT', 'OFFSET',
        'CREATE', 'TABLE', 'DROP', 'ALTER', 'ADD', 'COLUMN',
        'PRIMARY', 'KEY', 'FOREIGN', 'REFERENCES', 'INDEX',
        'DISTINCT', 'COUNT', 'SUM', 'AVG', 'MAX', 'MIN',
        'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'NULL', 'IS',
    ]
    
    # Build regex pattern for word boundaries
    for keyword in keywords:
        # Use word boundaries to avoid replacing parts of identifiers
        pattern = r'\b' + keyword + r'\b'
        query = re.sub(pattern, keyword, query, flags=re.IGNORECASE)
    
    return query.strip()


def fingerprint_sql(query: str, strip_comments: bool = True) -> str:
    """
    Generate a fingerprint for a SQL query.
    
    Normalizes the query first to ensure semantically identical queries
    produce the same fingerprint regardless of formatting.
    
    Args:
        query: SQL query string
        strip_comments: Whether to remove comments before fingerprinting
        
    Returns:
        Hexadecimal hash string (64 characters)
        
    Examples:
        >>> q1 = "SELECT * FROM users WHERE id = 1"
        >>> q2 = "select  *  from users where id=1"
        >>> fingerprint_sql(q1) == fingerprint_sql(q2)
        True
    """
    normalized = normalize_sql(query, strip_comments=strip_comments)
    return fingerprint(normalized)


def _default_serializer(obj: Any) -> Any:
    """
    Default serializer for objects that aren't JSON-serializable.
    
    Handles:
    - Datetime objects (ISO format)
    - Protobuf messages (with default values included for determinism)
    - Avro records
    - Generic objects with __dict__
    - Iterables
    
    Args:
        obj: Object to serialize
        
    Returns:
        Serializable representation
        
    Raises:
        TypeError: If object cannot be serialized
    """
    # Handle datetime objects
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    
    # Handle Protobuf messages
    if HAS_PROTOBUF and isinstance(obj, ProtobufMessage):
        return MessageToDict(
            obj,
            preserving_proto_field_name=True,
            including_default_value_fields=True,  # Critical for determinism
            use_integers_for_enums=False,  # Use enum names for readability
        )
    
    # Handle Avro records (fastavro specific types)
    if HAS_AVRO and hasattr(obj, '__class__') and hasattr(obj.__class__, '__avro_schema__'):
        # Avro record - serialize with schema info
        return _serialize_avro_record(obj)
    
    # Handle bytes (common in protobuf/avro)
    if isinstance(obj, bytes):
        # Base64 encode for JSON compatibility
        import base64
        return {"__bytes__": base64.b64encode(obj).decode('ascii')}
    
    # Handle sets (convert to sorted list for determinism)
    if isinstance(obj, set):
        return sorted(list(obj), key=lambda x: str(x))
    
    # Handle generic objects with __dict__
    if hasattr(obj, '__dict__'):
        return obj.__dict__
    
    # Handle iterables (excluding strings)
    if hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes)):
        return list(obj)
    
    raise TypeError(
        f"Object of type {type(obj).__name__} is not JSON serializable. "
        f"Consider adding custom serialization logic."
    )


def _serialize_avro_record(record: Any) -> Dict[str, Any]:
    """
    Serialize an Avro record to a canonical dict representation.
    
    Args:
        record: Avro record object
        
    Returns:
        Dictionary with schema info for deterministic serialization
    """
    schema = record.__class__.__avro_schema__
    
    # Convert record to dict, handling nested types properly
    result = {}
    for field in schema['fields']:
        field_name = field['name']
        if hasattr(record, field_name):
            value = getattr(record, field_name)
            # Recursively handle nested avro records
            if hasattr(value, '__avro_schema__'):
                result[field_name] = _serialize_avro_record(value)
            else:
                result[field_name] = value
    
    return result
