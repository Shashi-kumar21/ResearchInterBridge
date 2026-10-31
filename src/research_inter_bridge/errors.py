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


class ProfileError(ResearchInterBridgeError):
    """Raised when a student profile is invalid.

    The message must name the offending field and what a valid value looks like.
    """


class ProfileNotFoundError(ProfileError):
    """Raised when a profile is requested for a user id that has none."""


class DocumentError(ResearchInterBridgeError):
    """Raised when an uploaded document is rejected or cannot be used.

    The message must name the reason (extension, content, size, ownership) and
    never include document text.
    """


class UnsupportedDocumentError(DocumentError):
    """Raised when a file is not an accepted PDF/DOCX/TXT document."""


class DocumentTooLargeError(DocumentError):
    """Raised when a file exceeds the configured size limit."""


class DocumentNotFoundError(DocumentError):
    """Raised when a document id does not exist for the requesting user."""


class DocumentParsingError(DocumentError):
    """Raised when an accepted document cannot be converted to text."""
