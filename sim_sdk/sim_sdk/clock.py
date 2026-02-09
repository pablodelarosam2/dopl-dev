"""
SimClock - Deterministic time for replay.

Provides a clock that can be frozen to a specific time for deterministic
replay of time-dependent logic.
"""

import os
import threading
from datetime import datetime, timezone
from typing import Optional


class SimClock:
    """
    A clock that can be frozen for deterministic replay.

    In simulation mode, the clock returns a frozen time.
    In normal mode, it returns the real current time.

    Usage:
        from sim_sdk import sim_clock

        # Instead of datetime.now()
        now = sim_clock.now()

        # Freeze time for testing
        sim_clock.freeze(datetime(2024, 1, 1, 12, 0, 0))
        assert sim_clock.now() == datetime(2024, 1, 1, 12, 0, 0)

        # Unfreeze
        sim_clock.unfreeze()
    """

    def __init__(self, frozen_time: Optional[datetime] = None):
        """
        Initialize the SimClock.

        Args:
            frozen_time: If provided, the clock will always return this time
        """
        self._frozen_time: Optional[datetime] = frozen_time
        self._lock = threading.Lock()

    def now(self, tz: Optional[timezone] = None) -> datetime:
        """
        Get the current time.

        Args:
            tz: Timezone for the result. Defaults to None (naive datetime)

        Returns:
            Current time (frozen or real)
        """
        with self._lock:
            if self._frozen_time is not None:
                if tz is not None:
                    # Convert frozen time to requested timezone
                    if self._frozen_time.tzinfo is None:
                        # Assume UTC if no timezone
                        aware = self._frozen_time.replace(tzinfo=timezone.utc)
                    else:
                        aware = self._frozen_time
                    return aware.astimezone(tz)
                return self._frozen_time

        # Return real time
        if tz is not None:
            return datetime.now(tz)
        return datetime.now()

    def utcnow(self) -> datetime:
        """
        Get the current UTC time.

        Returns:
            Current UTC time (frozen or real)
        """
        with self._lock:
            if self._frozen_time is not None:
                if self._frozen_time.tzinfo is None:
                    return self._frozen_time
                return self._frozen_time.astimezone(timezone.utc).replace(tzinfo=None)

        return datetime.utcnow()

    def timestamp(self) -> float:
        """
        Get the current time as a Unix timestamp.

        Returns:
            Seconds since epoch
        """
        dt = self.now(tz=timezone.utc)
        return dt.timestamp()

    def freeze(self, dt: datetime) -> None:
        """
        Freeze the clock at a specific time.

        Args:
            dt: The time to freeze at
        """
        with self._lock:
            self._frozen_time = dt

    def unfreeze(self) -> None:
        """Unfreeze the clock to return real time."""
        with self._lock:
            self._frozen_time = None

    @property
    def is_frozen(self) -> bool:
        """Check if the clock is frozen."""
        with self._lock:
            return self._frozen_time is not None

    @property
    def frozen_time(self) -> Optional[datetime]:
        """Get the frozen time if set."""
        with self._lock:
            return self._frozen_time

    def __enter__(self) -> "SimClock":
        """Context manager entry."""
        return self

    def __exit__(self, *args) -> None:
        """Context manager exit - unfreeze the clock."""
        self.unfreeze()


def _create_clock_from_env() -> SimClock:
    """Create a SimClock from environment variables."""
    frozen_time_str = os.environ.get("SIM_FROZEN_TIME")

    if frozen_time_str:
        try:
            # Try ISO format first
            frozen_time = datetime.fromisoformat(frozen_time_str)
        except ValueError:
            try:
                # Try timestamp
                frozen_time = datetime.fromtimestamp(float(frozen_time_str))
            except ValueError:
                frozen_time = None
    else:
        frozen_time = None

    # Also check if we're in replay mode - if so, freeze at a default time
    sim_mode = os.environ.get("SIM_MODE", "off").lower()
    if sim_mode == "replay" and frozen_time is None:
        # Default frozen time for replay mode if not specified
        # Using a memorable date for debugging
        frozen_time = datetime(2024, 1, 1, 12, 0, 0)

    return SimClock(frozen_time=frozen_time)


# Global instance
sim_clock = _create_clock_from_env()


# Convenience functions that use the global clock
def now(tz: Optional[timezone] = None) -> datetime:
    """Get current time from the global SimClock."""
    return sim_clock.now(tz)


def utcnow() -> datetime:
    """Get current UTC time from the global SimClock."""
    return sim_clock.utcnow()


def timestamp() -> float:
    """Get current timestamp from the global SimClock."""
    return sim_clock.timestamp()


def freeze(dt: datetime) -> None:
    """Freeze the global SimClock."""
    sim_clock.freeze(dt)


def unfreeze() -> None:
    """Unfreeze the global SimClock."""
    sim_clock.unfreeze()
