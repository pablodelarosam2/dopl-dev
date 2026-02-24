"""
sim_capture() context manager for transport-agnostic dependency capture.

Does NOT wrap any client. Does NOT know about HTTP, gRPC, or any protocol.
Captures a labeled code block's result via an explicit set_result() call.

Record mode: block executes, developer calls cap.set_result(value),
value stored as stub in parent SimContext.

Replay mode: cap.replaying is True, cap.result returns recorded value.
Developer checks cap.replaying to skip the block body.

Off mode: block executes normally, CaptureHandle is a no-op.

Supports both sync (`with`) and async (`async with`) context managers.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .context import SimContext, SimMode, get_context
from .fixture.schema import FixtureEvent
from .trace import SimStubMissError, _make_serializable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fixture I/O for captures
# ---------------------------------------------------------------------------

def _capture_key(label: str, ordinal: int) -> str:
    """Build the relative path key for a capture fixture file.

    Layout: __capture__/{safe_label}_{ordinal}.json
    Prefixed with __capture__/ to distinguish from @sim_trace fixtures.
    """
    safe_label = label.replace(".", "_").replace("/", "_").replace(" ", "_")
    return f"__capture__/{safe_label}_{ordinal}.json"


def _write_capture(label: str, ordinal: int, result: Any, ctx: SimContext) -> None:
    """Persist a capture result to sink or stub_dir."""
    key = _capture_key(label, ordinal)

    if ctx.sink is not None:
        event = FixtureEvent(
            fixture_id=str(uuid.uuid4())[:8],
            qualname=f"capture:{label}",
            run_id=ctx.run_id,
            recorded_at=datetime.now(timezone.utc).isoformat(),
            output=_make_serializable(result),
            ordinal=ordinal,
            storage_key=key,
        )
        ctx.sink.emit(event)
        return

    if ctx.stub_dir is not None:
        data = {
            "type": "capture",
            "label": label,
            "ordinal": ordinal,
            "result": _make_serializable(result),
        }
        filepath = ctx.stub_dir / key
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return

    logger.debug("No sink or stub_dir — capture %r discarded", label)


def _read_capture(label: str, ordinal: int, stub_dir: Path) -> Optional[Dict[str, Any]]:
    """Read a recorded capture from stub_dir."""
    key = _capture_key(label, ordinal)
    filepath = stub_dir / key
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------------------
# CaptureHandle — the object yielded to the developer
# ---------------------------------------------------------------------------

class CaptureHandle:
    """Handle yielded by sim_capture() context manager.

    Attributes:
        replaying: True when in replay mode. Check this to skip block body.
        result: The recorded value (replay) or the value set via set_result() (record).
    """

    def __init__(self, label: str, ordinal: int, ctx: SimContext):
        self._label = label
        self._ordinal = ordinal
        self._ctx = ctx
        self._result: Any = None
        self._result_set: bool = False
        self.replaying: bool = ctx.is_replaying

        if self.replaying:
            self._load_recorded()

    def _load_recorded(self) -> None:
        """Load the recorded value from stub_dir during replay."""
        if self._ctx.stub_dir is None:
            raise SimStubMissError(
                f"capture:{self._label}", "", self._ordinal,
            )

        data = _read_capture(self._label, self._ordinal, self._ctx.stub_dir)
        if data is None:
            raise SimStubMissError(
                f"capture:{self._label}", "", self._ordinal, self._ctx.stub_dir,
            )

        self._result = data.get("result")
        self._result_set = True

    @property
    def result(self) -> Any:
        """Return the captured or recorded result."""
        return self._result

    def set_result(self, value: Any) -> None:
        """Store the result of this capture block.

        Must be called in record mode. Logged as warning if omitted.
        No-op in off mode.
        """
        self._result = value
        self._result_set = True


# ---------------------------------------------------------------------------
# Public API — sim_capture context manager
# ---------------------------------------------------------------------------

class sim_capture:
    """Transport-agnostic context manager for capturing dependency results.

    Record mode::

        with sim_capture("tax_rate_lookup") as cap:
            rate = tax_service.get_rate(zip_code)
            cap.set_result(rate)

    Replay mode::

        with sim_capture("tax_rate_lookup") as cap:
            if not cap.replaying:
                rate = tax_service.get_rate(zip_code)
                cap.set_result(rate)
            rate = cap.result

    Off mode: block runs normally, handle is a no-op.

    Args:
        label: Explicit string label for fingerprinting.
    """

    def __init__(self, label: str):
        self._label = label
        self._handle: Optional[CaptureHandle] = None
        self._ctx: Optional[SimContext] = None
        self._ordinal: int = 0

    def _setup(self) -> CaptureHandle:
        """Common setup for both sync and async entry."""
        self._ctx = get_context()

        if not self._ctx.is_active:
            # Off mode — return inert handle
            return CaptureHandle(self._label, 0, self._ctx)

        # Get ordinal for this label within the current scope
        self._ordinal = self._ctx.next_ordinal(f"capture:{self._label}")
        handle = CaptureHandle(self._label, self._ordinal, self._ctx)
        self._handle = handle
        return handle

    def _teardown(self) -> None:
        """Common teardown for both sync and async exit."""
        if self._ctx is None or not self._ctx.is_active or self._handle is None:
            return

        if self._ctx.is_recording:
            if not self._handle._result_set:
                logger.warning(
                    "sim_capture(%r): set_result() was not called in record mode",
                    self._label,
                )

            # Push to parent SimContext's collected_stubs
            self._ctx.collected_stubs.append({
                "type": "capture",
                "label": self._label,
                "ordinal": self._ordinal,
                "result": _make_serializable(self._handle._result),
            })

            # Write to disk for future replay
            _write_capture(
                self._label, self._ordinal, self._handle._result, self._ctx,
            )

        elif self._ctx.is_replaying:
            # In replay, push recorded value to parent stubs
            self._ctx.collected_stubs.append({
                "type": "capture",
                "label": self._label,
                "ordinal": self._ordinal,
                "result": _make_serializable(self._handle._result),
                "source": "replay",
            })

    # -- Sync context manager -----------------------------------------------

    def __enter__(self) -> CaptureHandle:
        return self._setup()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._teardown()

    # -- Async context manager ----------------------------------------------

    async def __aenter__(self) -> CaptureHandle:
        return self._setup()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._teardown()
