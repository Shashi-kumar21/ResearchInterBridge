"""JSON API for the web frontend (``task.md`` §5, ADR-012).

The API is a thin HTTP layer over the existing services: ``auth``, ``consent``,
``profiles`` and ``documents`` own every rule, so the browser can never bypass a
policy that the CLI enforces. Nothing here sends email.
"""

from .app import create_app

__all__ = ["create_app"]
