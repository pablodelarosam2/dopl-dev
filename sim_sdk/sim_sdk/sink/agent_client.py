"""
HTTP client for sending event batches to the local record-agent.

Uses only urllib.request from the standard library — no third-party
HTTP dependencies (requests, httpx, etc. are banned in the SDK).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import List

from .envelope import BatchRequest, BatchResponse, EventEnvelope

logger = logging.getLogger(__name__)


class AgentUnavailableError(Exception):
    """Raised when the agent endpoint cannot be reached."""


class AgentHttpClient:
    """Sends event batches to the local record-agent via HTTP POST.

    Targets POST /v1/events with a JSON body matching the agent's
    ingest.IngestRequest schema (PascalCase field names).
    """

    def __init__(
        self,
        agent_url: str = "http://localhost:9700",
        *,
        timeout_s: float = 5.0,
    ):
        self._endpoint = f"{agent_url.rstrip('/')}/v1/events"
        self._timeout_s = timeout_s

    def post_batch(self, envelopes: List[EventEnvelope]) -> BatchResponse:
        """POST a batch of envelopes to the agent.

        Returns:
            BatchResponse with accepted/dropped counts.

        Raises:
            AgentUnavailableError: Agent not reachable (connection refused,
                DNS failure, timeout).
            urllib.error.HTTPError: Agent returned an HTTP error status.
        """
        batch = BatchRequest(envelopes=envelopes)
        body = batch.serialize()

        req = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                resp_body = resp.read().decode("utf-8")
                return BatchResponse.from_wire(json.loads(resp_body))

        except urllib.error.HTTPError as exc:
            logger.warning(
                "Agent returned HTTP %d for POST %s", exc.code, self._endpoint,
            )
            raise

        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", str(exc))
            raise AgentUnavailableError(
                f"Agent unreachable at {self._endpoint}: {reason}"
            ) from exc
