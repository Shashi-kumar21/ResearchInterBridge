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
    ``code`` is a stable identifier an HTTP layer maps to a status code without
    matching on message text.
    """

    code = "invalid_profile"


class ProfileNotFoundError(ProfileError):
    """Raised when a profile is requested for a user id that has none."""

    code = "profile_missing"


class FactNotFoundError(ProfileError):
    """Raised when a fact id does not exist for the requesting user."""

    code = "fact_missing"


class DocumentError(ResearchInterBridgeError):
    """Raised when an uploaded document is rejected or cannot be used.

    The message must name the reason (extension, content, size, ownership) and
    never include document text. ``code`` is the stable HTTP-facing identifier.
    """

    code = "invalid_document"


class DocumentPurgeError(DocumentError):
    """Raised when stored files could not be removed during account deletion.

    Account deletion stops before deleting rows, so the student can retry after
    the file is released; the message names document ids, never paths or text.
    """

    code = "deletion_incomplete"


class UnsupportedDocumentError(DocumentError):
    """Raised when a file is not an accepted PDF/DOCX/TXT document."""

    code = "unsupported_document"


class DocumentTooLargeError(DocumentError):
    """Raised when a file exceeds the configured size limit."""

    code = "document_too_large"


class DocumentNotFoundError(DocumentError):
    """Raised when a document id does not exist for the requesting user."""

    code = "document_missing"


class DocumentParsingError(DocumentError):
    """Raised when an accepted document cannot be converted to text."""

    code = "document_unreadable"


class AuthError(ResearchInterBridgeError):
    """Raised when credentials, a session or an account operation fail.

    Messages must stay actionable without revealing whether an email is
    registered, and must never contain a password or session token.

    ``code`` is a stable, non-secret identifier so an HTTP layer can map the
    failure to a status code without matching on message text:
    ``invalid_input``, ``email_taken``, ``invalid_credentials``,
    ``account_locked``, ``invalid_session``, ``wrong_password`` and
    ``account_missing``.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "auth_error",
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        #: Whole seconds the caller should wait before retrying, when known
        #: (currently only ``account_locked``).
        self.retry_after = retry_after


class ConsentError(ResearchInterBridgeError):
    """Raised when required consent is missing, unknown or out of date.

    ``code`` is ``consent_required`` or ``unknown_consent`` so callers can tell a
    missing acceptance from a typo in a consent kind.
    """

    def __init__(self, message: str, *, code: str = "consent_required") -> None:
        super().__init__(message)
        self.code = code


class RateLimitError(ResearchInterBridgeError):
    """Raised when a caller has spent the request budget for a route.

    ``retry_after`` is whole seconds and is sent verbatim as the ``Retry-After``
    header, so a client can wait instead of retrying blindly.
    """

    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


class CacheError(ResearchInterBridgeError):
    """Raised when a shared-cache key, TTL or provenance value is invalid."""


class DiscoveryError(ResearchInterBridgeError):
    """Raised when a research query or a provider record is invalid.

    The message must name the field and what a valid value looks like; it must
    never echo a provider payload or a student's private data.
    """

    code = "invalid_discovery"


class DiscoveryUnavailableError(DiscoveryError):
    """Raised when no source is configured and the cache has nothing to serve.

    Callers should either configure an adapter or ask for cache-only browsing,
    so the failure is actionable instead of an empty page.
    """

    code = "discovery_unavailable"


class SourceFetchError(DiscoveryError):
    """Raised when a live source cannot be fetched safely.

    The message names the host and the status or reason, never the response
    body: a page can contain private or untrusted text and is not an error
    message. ``code`` stays ``discovery_unavailable`` so an HTTP caller sees the
    same retryable class as a missing adapter.
    """

    code = "discovery_unavailable"


class ProgramNotFoundError(DiscoveryError):
    """Raised when a program id is not in the shared cache."""

    code = "program_missing"


class InstitutionNotFoundError(DiscoveryError):
    """Raised when an institution id is not in the shared cache."""

    code = "institution_missing"
