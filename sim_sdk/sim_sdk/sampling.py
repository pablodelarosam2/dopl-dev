"""
SDK-level sampling gate for fixture recording.

Controls the rate at which requests are captured as fixtures.
Reads `SIM_SAMPLE_RATE` from environment (default: 1.0 = 100%).

This is the first layer of the two-layer sampling strategy
(see Phase 3 spec, Section 5). The Indexer provides the second
layer via content-hash deduplication.

Zero framework dependencies -- standard library only.
"""

import logging
import os
import random

logger = logging.getLogger(__name__)


def get_sample_rate() -> float:
    """Read and validate SIM_SAMPLE_RATE from environment.

    Returns:
        Float between 0.0 and 1.0 inclusive.
        Defaults to 1.0 if not set or invalid.
    """
    raw = os.environ.get("SIM_SAMPLE_RATE", "1.0")
    try:
        rate = float(raw)
    except (ValueError, TypeError):
        logger.warning(
            "Invalid SIM_SAMPLE_RATE=%r, defaulting to 1.0", raw,
        )
        return 1.0

    # Clamp to [0.0, 1.0]
    if rate < 0.0:
        return 0.0
    if rate > 1.0:
        return 1.0
    return rate


def should_record() -> bool:
    """Decide whether to record this request based on SIM_SAMPLE_RATE.

    Returns True if the request should be captured, False to skip.

    The check uses random.random() < rate, so:
      - rate=1.0 -> always True  (backward compat with Phase 2)
      - rate=0.0 -> always False
      - rate=0.5 -> ~50% of calls return True
    """
    rate = get_sample_rate()
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate
