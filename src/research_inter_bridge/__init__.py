"""ResearchInterBridge."""

from __future__ import annotations

from .config import Settings, load_settings
from .errors import ConfigError, HealthCheckError, MigrationError, ResearchInterBridgeError
from .health import HealthReport, check_health

__version__ = "0.1.0"

__all__ = [
    "ConfigError",
    "HealthCheckError",
    "HealthReport",
    "MigrationError",
    "ResearchInterBridgeError",
    "Settings",
    "__version__",
    "check_health",
    "load_settings",
]
