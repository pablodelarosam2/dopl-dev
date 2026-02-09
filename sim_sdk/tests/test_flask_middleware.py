"""Tests for sim_sdk.flask_middleware module."""

import json

import pytest
from flask import Flask, jsonify, request

from sim_sdk.context import SimContext, SimMode, clear_context, set_context
from sim_sdk.flask_middleware import (
    get_sim_request_id,
    get_sim_run_id,
    sim_capture,
    sim_middleware,
)
from sim_sdk.store import StubStore


@pytest.fixture
def app():
    """Create a Flask test app."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/echo", methods=["POST"])
    def echo():
        data = request.get_json()
        return jsonify({"received": data})

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"})

    return app


@pytest.fixture
def app_with_middleware(app):
    """Flask app with simulation middleware."""
    sim_middleware(app)
    return app


@pytest.fixture
def app_with_decorator():
    """Flask app with selective capture decorator."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/decorated", methods=["POST"])
    @sim_capture()
    def decorated():
        data = request.get_json()
        return jsonify({"processed": data})

    @app.route("/not-decorated", methods=["POST"])
    def not_decorated():
        data = request.get_json()
        return jsonify({"processed": data})

    return app


class TestSimMiddleware:
    """Tests for sim_middleware function."""

    def test_off_mode_passthrough(self, app_with_middleware, sim_context_off):
        client = app_with_middleware.test_client()

        response = client.post(
            "/echo",
            json={"message": "hello"},
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json == {"received": {"message": "hello"}}

        # No sim headers in off mode
        assert "X-Sim-Request-Id" not in response.headers

    def test_record_mode_captures(self, app_with_middleware, sim_context_record):
        client = app_with_middleware.test_client()

        response = client.post(
            "/echo",
            json={"message": "test"},
            content_type="application/json",
        )

        assert response.status_code == 200

        # Should have sim headers
        assert "X-Sim-Request-Id" in response.headers
        assert "X-Sim-Run-Id" in response.headers

        # Should have saved to store
        store = StubStore(sim_context_record.stub_dir)
        requests = store.list_requests()
        assert len(requests) == 1

        # Verify captured data
        request_id = response.headers["X-Sim-Request-Id"]
        captured = store.load_request(request_id)

        assert captured["request"]["method"] == "POST"
        assert captured["request"]["path"] == "/echo"
        assert captured["response"]["status_code"] == 200

    def test_replay_mode_passthrough(self, app_with_middleware, sim_context_replay):
        client = app_with_middleware.test_client()

        response = client.post(
            "/echo",
            json={"message": "test"},
            content_type="application/json",
        )

        assert response.status_code == 200
        assert "X-Sim-Request-Id" in response.headers

    def test_captures_get_request(self, app_with_middleware, sim_context_record):
        client = app_with_middleware.test_client()

        response = client.get("/health")

        assert response.status_code == 200

        store = StubStore(sim_context_record.stub_dir)
        requests = store.list_requests()
        assert len(requests) == 1


class TestSimCaptureDecorator:
    """Tests for sim_capture decorator."""

    def test_decorated_endpoint_captures(self, app_with_decorator, sim_context_record):
        client = app_with_decorator.test_client()

        response = client.post(
            "/decorated",
            json={"value": 123},
            content_type="application/json",
        )

        assert response.status_code == 200
        assert "X-Sim-Request-Id" in response.headers

        store = StubStore(sim_context_record.stub_dir)
        assert len(store.list_requests()) == 1

    def test_non_decorated_endpoint_no_capture(
        self, app_with_decorator, sim_context_record
    ):
        client = app_with_decorator.test_client()

        response = client.post(
            "/not-decorated",
            json={"value": 123},
            content_type="application/json",
        )

        assert response.status_code == 200

        # Not decorated, so no capture even in record mode
        # Note: This requires that sim_middleware is not added to this app
        assert "X-Sim-Request-Id" not in response.headers


class TestCaptureContent:
    """Tests for captured content accuracy."""

    def test_request_body_captured(self, app_with_middleware, sim_context_record):
        client = app_with_middleware.test_client()

        payload = {"items": [1, 2, 3], "user": {"name": "Alice"}}
        response = client.post(
            "/echo",
            json=payload,
            content_type="application/json",
        )

        store = StubStore(sim_context_record.stub_dir)
        request_id = response.headers["X-Sim-Request-Id"]
        captured = store.load_request(request_id)

        assert captured["request"]["body"] == payload

    def test_response_body_captured(self, app_with_middleware, sim_context_record):
        client = app_with_middleware.test_client()

        response = client.post(
            "/echo",
            json={"input": "test"},
            content_type="application/json",
        )

        store = StubStore(sim_context_record.stub_dir)
        request_id = response.headers["X-Sim-Request-Id"]
        captured = store.load_request(request_id)

        assert captured["response"]["body"] == {"received": {"input": "test"}}

    def test_fingerprint_generated(self, app_with_middleware, sim_context_record):
        client = app_with_middleware.test_client()

        response = client.post(
            "/echo",
            json={"test": True},
            content_type="application/json",
        )

        store = StubStore(sim_context_record.stub_dir)
        request_id = response.headers["X-Sim-Request-Id"]
        captured = store.load_request(request_id)

        assert "fingerprint" in captured["request"]
        assert len(captured["request"]["fingerprint"]) == 16

    def test_duration_captured(self, app_with_middleware, sim_context_record):
        client = app_with_middleware.test_client()

        response = client.get("/health")

        store = StubStore(sim_context_record.stub_dir)
        request_id = response.headers["X-Sim-Request-Id"]
        captured = store.load_request(request_id)

        assert "duration_ms" in captured
        assert captured["duration_ms"] >= 0


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_sim_request_id_in_context(self, app_with_middleware, sim_context_record):
        client = app_with_middleware.test_client()

        with app_with_middleware.test_request_context():
            # Outside of a request, should be None
            assert get_sim_request_id() is None

    def test_get_sim_run_id(self, sim_context_record):
        run_id = get_sim_run_id()
        assert run_id == "test-run-123"

    def test_get_sim_run_id_off_mode(self, sim_context_off):
        run_id = get_sim_run_id()
        assert run_id is None
