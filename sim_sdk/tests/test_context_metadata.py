"""Tests for HTTP metadata fields on SimContext."""

from sim_sdk.context import SimContext, SimMode, set_context, get_context, clear_context

import pytest


@pytest.fixture(autouse=True)
def clean_context():
    clear_context()
    yield
    clear_context()


class TestContextHttpMetadata:
    def test_defaults_to_empty(self):
        """http_method and http_path default to empty strings."""
        ctx = SimContext(mode=SimMode.RECORD, run_id="test")
        assert ctx.http_method == ""
        assert ctx.http_path == ""

    def test_can_set_values(self):
        """http_method and http_path can be set at construction."""
        ctx = SimContext(
            mode=SimMode.RECORD, run_id="test",
            http_method="POST", http_path="/quote",
        )
        assert ctx.http_method == "POST"
        assert ctx.http_path == "/quote"

    def test_reset_clears_metadata(self):
        """reset() clears http_method and http_path."""
        ctx = SimContext(
            mode=SimMode.RECORD, run_id="test",
            http_method="POST", http_path="/quote",
        )
        ctx.reset()
        assert ctx.http_method == ""
        assert ctx.http_path == ""

    def test_start_new_request_clears_metadata(self):
        """start_new_request() clears http_method and http_path."""
        ctx = SimContext(
            mode=SimMode.RECORD, run_id="test",
            http_method="POST", http_path="/quote",
        )
        ctx.start_new_request()
        assert ctx.http_method == ""
        assert ctx.http_path == ""
