"""
Fixture model and serialization.
"""

from .schema import Fixture, CaptureRecord, TraceRecord
from .writer import FixtureWriter

__all__ = ['Fixture', 'CaptureRecord', 'TraceRecord', 'FixtureWriter']
