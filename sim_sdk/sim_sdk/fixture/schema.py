"""
Fixture file schemas and data models.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from datetime import datetime


@dataclass
class TraceRecord:
    """
    A single traced function call.
    """
    function_name: str
    args: List[Any]
    kwargs: Dict[str, Any]
    result: Any
    exception: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CaptureRecord:
    """
    A captured block of operations.
    """
    name: str
    captures: List[Any] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    duration_ms: Optional[float] = None


@dataclass
class Fixture:
    """
    A complete fixture file containing all recorded operations.
    """
    fixture_id: str
    timestamp: str
    traces: List[TraceRecord] = field(default_factory=list)
    captures: List[CaptureRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert fixture to dictionary for serialization."""
        return {
            'fixture_id': self.fixture_id,
            'timestamp': self.timestamp,
            'traces': [self._trace_to_dict(t) for t in self.traces],
            'captures': [self._capture_to_dict(c) for c in self.captures],
            'metadata': self.metadata
        }
    
    def _trace_to_dict(self, trace: TraceRecord) -> Dict[str, Any]:
        """Convert trace record to dictionary."""
        result = {
            'function_name': trace.function_name,
            'args': trace.args,
            'kwargs': trace.kwargs,
            'result': trace.result,
            'timestamp': trace.timestamp
        }
        if trace.exception:
            result['exception'] = trace.exception
        if trace.duration_ms:
            result['duration_ms'] = trace.duration_ms
        if trace.metadata:
            result['metadata'] = trace.metadata
        return result
    
    def _capture_to_dict(self, capture: CaptureRecord) -> Dict[str, Any]:
        """Convert capture record to dictionary."""
        result = {
            'name': capture.name,
            'captures': capture.captures,
            'timestamp': capture.timestamp
        }
        if capture.duration_ms:
            result['duration_ms'] = capture.duration_ms
        if capture.metadata:
            result['metadata'] = capture.metadata
        return result
