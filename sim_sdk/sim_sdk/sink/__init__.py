"""
Recording sink interfaces and implementations.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class RecordSink(ABC):
    """
    Abstract base class for recording sinks.
    
    A sink is responsible for persisting recorded fixtures.
    """
    
    @abstractmethod
    def write(self, fixture_id: str, data: Dict[str, Any]) -> None:
        """
        Write a fixture to the sink.
        
        Args:
            fixture_id: Unique identifier for the fixture
            data: Fixture data (JSON-serializable dict)
        """
        pass
    
    @abstractmethod
    def flush(self) -> None:
        """
        Flush any buffered data to the underlying storage.
        """
        pass
    
    @abstractmethod
    def close(self) -> None:
        """
        Close the sink and release any resources.
        """
        pass


__all__ = ['RecordSink']
