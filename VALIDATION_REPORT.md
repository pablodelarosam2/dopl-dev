# sim-sdk Package Validation Report

## ✅ Zero Framework Dependencies - VALIDATED

### Constraint Validation

The `sim_sdk/` package has been validated to contain **ZERO imports from web frameworks, HTTP libraries, or database drivers**.

### What the SDK DOES Import (All Allowed)

#### Standard Library Only:
- `json` - JSON serialization
- `hashlib` - SHA-256 fingerprinting  
- `os` - Environment variables and file system
- `threading` - Thread-local storage
- `uuid` - Unique ID generation
- `time` - Timing and duration
- `functools` - Decorator utilities
- `inspect` - Function signature introspection
- `dataclasses` - Data structures
- `pathlib` - Path manipulation
- `datetime` - Timestamps
- `typing` - Type hints
- `enum` - Enumerations
- `contextlib` - Context managers
- `abc` - Abstract base classes
- `copy` - Deep copying
- `re` - Regular expressions

#### Optional Dependencies (Properly Isolated):

1. **boto3** (S3 only)
   - Location: `sim_sdk/sink/s3.py` only
   - Lazy imported: Only when `S3Sink.s3_client` property is accessed
   - Graceful failure: Raises clear error if not installed
   
2. **jsonpath-ng** (Redaction only)
   - Location: `sim_sdk/redaction.py` only
   - Try/except import: Falls back to simple key-based redaction
   - Fully optional: Works without it
   
3. **PyYAML** (Config only)
   - Location: `sim_sdk/config.py` only
   - Try/except import: Falls back to JSON format
   - Fully optional: Users can use JSON config files

### What the SDK Does NOT Import (Banned List)

✅ **None of these are imported anywhere in sim_sdk/**:

- ❌ flask
- ❌ django  
- ❌ fastapi
- ❌ starlette
- ❌ requests
- ❌ httpx
- ❌ aiohttp
- ❌ psycopg2 / psycopg
- ❌ sqlalchemy
- ❌ asyncpg
- ❌ pymongo
- ❌ redis
- ❌ celery

### Package Boundary

**The SDK provides primitives:**
- `@sim_trace` - Function tracing decorator
- `sim_capture()` - Operation capture context manager
- `sim_db()` - Database connection wrapper (generic)

**The consumer** (Flask app, Django app, plain script) **uses those primitives** in whatever framework they choose.

**Examples** in `examples/` show how to integrate, but `examples/` is **NOT part of the SDK package**.

### CI Enforcement Ready

This validation can be automated in CI:

```bash
#!/bin/bash
# In CI pipeline: fail if sim_sdk/ imports any banned module

BANNED_MODULES="flask|django|fastapi|starlette|requests|httpx|aiohttp|psycopg2|sqlalchemy|asyncpg|pymongo|redis|celery"

if grep -r -E "^(import|from)\s+($BANNED_MODULES)" sim_sdk/sim_sdk/ --include="*.py"; then
    echo "❌ VIOLATION: sim_sdk imports banned framework/driver modules"
    exit 1
else
    echo "✅ PASSED: No banned imports found in sim_sdk/"
    exit 0
fi
```

### Files Validated

```
sim_sdk/sim_sdk/
├── __init__.py ✅
├── canonical.py ✅
├── capture.py ✅
├── config.py ✅
├── context.py ✅
├── db.py ✅
├── redaction.py ✅
├── trace.py ✅
├── fixture/
│   ├── __init__.py ✅
│   ├── schema.py ✅
│   └── writer.py ✅
└── sink/
    ├── __init__.py ✅
    ├── local.py ✅
    └── s3.py ✅
```

**Total Files Validated: 14**  
**Violations Found: 0**  
**Status: ✅ COMPLIANT**

---

*Generated: 2026-02-13*
*Validation Rule: Zero web framework, HTTP library, or database driver imports in sim_sdk/*
