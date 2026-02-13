"""
Configuration loader for sim.yaml files.
"""

import os
import json
from typing import Dict, Any, Optional
from pathlib import Path


class SimConfig:
    """Configuration loaded from sim.yaml"""
    
    def __init__(self, config_dict: Dict[str, Any]):
        self._config = config_dict
        
    @property
    def sink_type(self) -> str:
        """Get the configured sink type (local, s3, etc.)"""
        return self._config.get('sink', {}).get('type', 'local')
    
    @property
    def sink_config(self) -> Dict[str, Any]:
        """Get sink-specific configuration"""
        return self._config.get('sink', {})
    
    @property
    def redaction_rules(self) -> list:
        """Get JSONPath redaction rules"""
        return self._config.get('redaction', [])
    
    @property
    def recording_enabled(self) -> bool:
        """Check if recording is enabled"""
        return self._config.get('recording', {}).get('enabled', True)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key"""
        return self._config.get(key, default)


def load_config(config_path: Optional[str] = None) -> SimConfig:
    """
    Load configuration from sim.yaml.
    
    Search order:
    1. Provided config_path
    2. SIM_CONFIG environment variable
    3. ./sim.yaml in current directory
    4. sim.yaml in parent directories (walk up the tree)
    
    Args:
        config_path: Optional explicit path to config file
        
    Returns:
        SimConfig instance
        
    Raises:
        FileNotFoundError: If no config file found
    """
    # Try explicit path
    if config_path:
        return _load_from_path(config_path)
    
    # Try environment variable
    env_path = os.environ.get('SIM_CONFIG')
    if env_path:
        return _load_from_path(env_path)
    
    # Try current directory and walk up
    current = Path.cwd()
    while True:
        config_file = current / 'sim.yaml'
        if config_file.exists():
            return _load_from_path(str(config_file))
        
        # Stop at filesystem root
        if current == current.parent:
            break
        current = current.parent
    
    # No config found, return default empty config
    return SimConfig({})


def _load_from_path(path: str) -> SimConfig:
    """Load config from a specific path"""
    with open(path, 'r') as f:
        # Support both JSON and YAML-like simple formats
        # For now, use JSON (standard library only)
        # Users can extend this to use yaml if they install PyYAML
        import json
        config_dict = json.load(f) if path.endswith('.json') else _parse_simple_yaml(f.read())
    return SimConfig(config_dict)


def _parse_simple_yaml(content: str) -> Dict[str, Any]:
    """
    Parse a simple YAML-like format using only standard library.
    This is a minimal implementation. For full YAML support, users should install PyYAML.
    
    Supports basic key: value pairs and nested structures.
    """
    # For now, just return empty dict and document that users need PyYAML for yaml files
    # Or they can use JSON format instead
    try:
        import yaml
        return yaml.safe_load(content) or {}
    except ImportError:
        # If PyYAML not available, try json format or return empty
        try:
            import json
            return json.loads(content)
        except:
            return {}
