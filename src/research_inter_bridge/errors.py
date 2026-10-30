"""Explicit, actionable exception types used across the package."""

from __future__ import annotations


class ResearchInterBridgeError(Exception):
    """Base class for every error raised deliberately by this package."""


class ConfigError(ResearchInterBridgeError):
    """Raised when configuration is missing or invalid.

    The message must tell the operator which setting is wrong and how to fix it.
    """


class MigrationError(ResearchInterBridgeError):
    """Raised when the database schema cannot be migrated safely."""


class HealthCheckError(ResearchInterBridgeError):
    """Raised when a health check cannot be executed at all."""
