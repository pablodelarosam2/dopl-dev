"""
Flask middleware for capturing request/response data.

Provides both middleware (for app-wide instrumentation) and a decorator
(for selective instrumentation).
"""

import json
import time
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from flask import Flask, Request, Response, g, request

from sim_sdk.canonicalize import canonicalize, fingerprint
from sim_sdk.context import SimContext, get_context
from sim_sdk.redaction import redact
from sim_sdk.store import StubStore


# Headers to include in fingerprints (lowercase)
DEFAULT_FINGERPRINT_HEADERS = [
    "content-type",
    "accept",
]

# Headers to exclude from capture (lowercase)
EXCLUDED_HEADERS = [
    "authorization",
    "cookie",
    "x-api-key",
]


def sim_middleware(
    app: Flask,
    fingerprint_headers: Optional[List[str]] = None,
    redact_paths: Optional[List[str]] = None,
) -> None:
    """
    Register simulation middleware on a Flask app.

    This captures all requests and responses when simulation mode is active.

    Args:
        app: Flask application instance
        fingerprint_headers: Headers to include in request fingerprints
        redact_paths: JSONPaths to redact from captured data
    """
    if fingerprint_headers is None:
        fingerprint_headers = DEFAULT_FINGERPRINT_HEADERS

    @app.before_request
    def before_sim_request():
        """Capture request data before processing."""
        ctx = get_context()

        if not ctx.is_active:
            return

        # Generate request ID and store in Flask's g object
        request_id = ctx.new_request_id()
        g.sim_request_id = request_id
        g.sim_start_time = time.time()

        # Capture request data
        g.sim_request_data = _capture_request(request, fingerprint_headers)

        # Add request ID header to response (will be done in after_request)

    @app.after_request
    def after_sim_request(response: Response) -> Response:
        """Capture response data after processing."""
        ctx = get_context()

        if not ctx.is_active:
            return response

        request_id = getattr(g, "sim_request_id", None)
        if not request_id:
            return response

        # Add sim request ID to response headers
        response.headers["X-Sim-Request-Id"] = request_id
        response.headers["X-Sim-Run-Id"] = ctx.run_id

        # Capture response data
        request_data = getattr(g, "sim_request_data", {})
        response_data = _capture_response(response)

        # Calculate duration
        start_time = getattr(g, "sim_start_time", time.time())
        duration_ms = (time.time() - start_time) * 1000

        # Build complete capture
        capture = {
            "request_id": request_id,
            "run_id": ctx.run_id,
            "request": request_data,
            "response": response_data,
            "duration_ms": round(duration_ms, 2),
        }

        # Apply redaction
        if redact_paths:
            capture = redact(capture, paths=redact_paths)

        # Store in record mode
        if ctx.is_recording and ctx.stub_dir:
            store = StubStore(ctx.stub_dir)
            store.save_request(request_id, capture)

        return response

    return None


def sim_capture(
    fingerprint_headers: Optional[List[str]] = None,
    redact_paths: Optional[List[str]] = None,
) -> Callable:
    """
    Decorator for capturing individual endpoint request/response.

    Use this when you want selective capture instead of app-wide middleware.

    Args:
        fingerprint_headers: Headers to include in fingerprint
        redact_paths: JSONPaths to redact from captured data

    Returns:
        Decorator function

    Example:
        @app.route("/quote", methods=["POST"])
        @sim_capture(redact_paths=["$.user.email"])
        def quote():
            ...
    """
    if fingerprint_headers is None:
        fingerprint_headers = DEFAULT_FINGERPRINT_HEADERS

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs):
            ctx = get_context()

            if not ctx.is_active:
                return f(*args, **kwargs)

            # Generate request ID
            request_id = ctx.new_request_id()
            g.sim_request_id = request_id
            g.sim_start_time = time.time()

            # Capture request
            request_data = _capture_request(request, fingerprint_headers)

            # Execute the actual function
            response = f(*args, **kwargs)

            # Ensure we have a Response object
            if not isinstance(response, Response):
                # Convert to Response if needed
                from flask import make_response
                response = make_response(response)

            # Add sim headers
            response.headers["X-Sim-Request-Id"] = request_id
            response.headers["X-Sim-Run-Id"] = ctx.run_id

            # Capture response
            response_data = _capture_response(response)

            # Calculate duration
            duration_ms = (time.time() - g.sim_start_time) * 1000

            # Build capture
            capture = {
                "request_id": request_id,
                "run_id": ctx.run_id,
                "request": request_data,
                "response": response_data,
                "duration_ms": round(duration_ms, 2),
            }

            # Apply redaction
            if redact_paths:
                capture = redact(capture, paths=redact_paths)

            # Store in record mode
            if ctx.is_recording and ctx.stub_dir:
                store = StubStore(ctx.stub_dir)
                store.save_request(request_id, capture)

            return response

        return wrapper
    return decorator


def _capture_request(
    req: Request,
    fingerprint_headers: List[str],
) -> Dict[str, Any]:
    """
    Capture relevant request data.

    Args:
        req: Flask Request object
        fingerprint_headers: Headers to include

    Returns:
        Dictionary with request data
    """
    # Get body
    body = None
    if req.is_json:
        try:
            body = req.get_json(silent=True)
        except Exception:
            pass
    elif req.data:
        try:
            body = req.data.decode("utf-8")
        except Exception:
            body = "<binary>"

    # Get selected headers (lowercase keys)
    headers = {}
    for key in fingerprint_headers:
        value = req.headers.get(key)
        if value:
            headers[key.lower()] = value

    # Build request data
    data = {
        "method": req.method,
        "path": req.path,
        "query_string": req.query_string.decode("utf-8") if req.query_string else None,
    }

    if headers:
        data["headers"] = headers

    if body is not None:
        data["body"] = body

    # Generate fingerprint
    data["fingerprint"] = fingerprint({
        "method": req.method,
        "path": req.path,
        "body": body,
        "headers": headers,
    })

    return data


def _capture_response(resp: Response) -> Dict[str, Any]:
    """
    Capture relevant response data.

    Args:
        resp: Flask Response object

    Returns:
        Dictionary with response data
    """
    # Get body
    body = None
    content_type = resp.content_type or ""

    if "application/json" in content_type:
        try:
            body = resp.get_json(silent=True)
        except Exception:
            try:
                body = resp.get_data(as_text=True)
            except Exception:
                pass
    elif "text/" in content_type:
        try:
            body = resp.get_data(as_text=True)
        except Exception:
            pass

    # Get relevant headers
    headers = {
        "content-type": resp.content_type,
    }

    data = {
        "status_code": resp.status_code,
        "headers": headers,
    }

    if body is not None:
        data["body"] = body

    return data


def get_sim_request_id() -> Optional[str]:
    """
    Get the current simulation request ID.

    Returns:
        Request ID or None if not in simulation mode
    """
    return getattr(g, "sim_request_id", None)


def get_sim_run_id() -> Optional[str]:
    """
    Get the current simulation run ID.

    Returns:
        Run ID or None if not in simulation mode
    """
    ctx = get_context()
    return ctx.run_id if ctx.is_active else None
