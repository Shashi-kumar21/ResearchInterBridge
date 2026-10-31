"""Student document storage: validation, safe paths and per-user isolation.

Phase 2b of ``task.md`` (§3.3, §8). Rules encoded here:

* The client's extension and MIME type are **not** trusted: the first bytes are
  sniffed and must agree with the extension, otherwise the upload is rejected.
* The stored file name is derived from the content hash and the user id, never
  from the client's file name, so a crafted name cannot escape the data
  directory.
* Documents are per-user data: every read and delete is scoped by ``user_id`` and
  a missing/foreign id raises instead of returning another student's file.
* The same bytes uploaded twice by the same user are stored once (idempotent
  re-upload), because students re-upload after a failed parse.
* Nothing in this module logs or audits document text. Audit metadata carries
  ids, sizes and hashes only.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import sqlite3
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db
from .errors import (
    DocumentError,
    DocumentNotFoundError,
    DocumentTooLargeError,
    UnsupportedDocumentError,
)
from .logging import get_logger

logger = get_logger(__name__)

#: Hard limit for one uploaded file. Big enough for a long CV with appendices,
#: small enough that a hostile upload cannot exhaust memory or disk.
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

#: Uncompressed DOCX content above this size is treated as a zip bomb.
MAX_DOCX_UNCOMPRESSED_BYTES = 60 * 1024 * 1024
MAX_DOCX_ENTRIES = 5_000

MAX_DISPLAY_NAME_LENGTH = 200
DOCUMENT_KINDS = ("cv", "transcript", "other")

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MEDIA_TYPE = "text/plain"

#: Extension -> media type. An upload must match one of these.
ALLOWED_EXTENSIONS: Mapping[str, str] = {
    ".pdf": PDF_MEDIA_TYPE,
    ".docx": DOCX_MEDIA_TYPE,
    ".txt": TEXT_MEDIA_TYPE,
}

#: Media types we accept when the client declares one. Generic values are
#: tolerated because browsers and PowerShell send them inconsistently.
_GENERIC_MEDIA_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """One stored document. ``storage_path`` is relative to the data directory."""

    id: int
    user_id: int
    kind: str
    display_name: str
    media_type: str
    extension: str
    size_bytes: int
    sha256: str
    storage_path: str
    status: str
    parse_error: str | None
    uploaded_at: str
    parsed_at: str | None

    @property
    def is_parsed(self) -> bool:
        return self.status == "parsed"


def _clean_display_name(raw: str) -> str:
    """Keep a safe, human-readable name; never a path."""

    if not isinstance(raw, str) or not raw.strip():
        raise DocumentError("display_name is required and cannot be blank.")
    # Both separators, so a Windows-style path cannot survive on POSIX.
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_CHARS.sub("", name)
    name = " ".join(name.split())
    # Strip leading dots so the name cannot become a hidden/relative entry.
    name = name.lstrip(".")
    if not name:
        raise DocumentError("display_name must contain a file name, not only a path.")
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        raise DocumentError(
            f"display_name must be at most {MAX_DISPLAY_NAME_LENGTH} characters; got {len(name)}."
        )
    return name


def _extension_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UnsupportedDocumentError(
            f"unsupported file extension {suffix or '(none)'!r}; allowed: {allowed}."
        )
    return suffix


def _docx_media_type(payload: bytes) -> str | None:
    """Return the DOCX media type only for a real Word document zip."""

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            if len(names) > MAX_DOCX_ENTRIES:
                raise UnsupportedDocumentError(
                    f"archive contains {len(names)} entries; limit is {MAX_DOCX_ENTRIES}."
                )
            uncompressed = sum(info.file_size for info in archive.infolist())
            if uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise UnsupportedDocumentError(
                    "archive expands to more than "
                    f"{MAX_DOCX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB; refusing it."
                )
            if "word/document.xml" in names and "[Content_Types].xml" in names:
                return DOCX_MEDIA_TYPE
    except zipfile.BadZipFile:
        return None
    return None


def _looks_like_text(payload: bytes) -> bool:
    # UTF-16 text is full of NUL bytes, so the BOM decides before the NUL check.
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            payload.decode("utf-16")
        except UnicodeDecodeError:
            return False
        return True
    if b"\x00" in payload:
        return False
    for encoding in ("utf-8", "latin-1"):
        try:
            payload.decode(encoding)
        except UnicodeDecodeError:
            continue
        return True
    return False


def sniff_media_type(payload: bytes) -> str | None:
    """Detect the real media type from content, ignoring the client's claim."""

    if payload.startswith(b"%PDF-"):
        return PDF_MEDIA_TYPE
    if payload.startswith(b"PK\x03\x04"):
        return _docx_media_type(payload)
    if _looks_like_text(payload):
        return TEXT_MEDIA_TYPE
    return None


def validate_document(
    display_name: str,
    payload: bytes,
    *,
    declared_media_type: str | None = None,
) -> tuple[str, str, str]:
    """Validate an upload and return ``(display_name, extension, media_type)``.

    Raises :class:`UnsupportedDocumentError` or :class:`DocumentTooLargeError`
    with an actionable message. The check order matters: size first so a huge
    file is rejected before it is decoded or unzipped.
    """

    name = _clean_display_name(display_name)
    extension = _extension_of(name)
    if not payload:
        raise UnsupportedDocumentError(f"{name} is empty; upload a non-empty file.")
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLargeError(
            f"{name} is {len(payload)} bytes; the limit is {MAX_DOCUMENT_BYTES} bytes."
        )

    sniffed = sniff_media_type(payload)
    if sniffed is None:
        raise UnsupportedDocumentError(
            f"{name} is not a PDF, DOCX or UTF-8/UTF-16 text file; "
            "the content does not match any accepted format."
        )

    expected = ALLOWED_EXTENSIONS[extension]
    # A .txt file may legitimately be reported as text/plain; nothing else may
    # disagree with its own content.
    if sniffed != expected:
        raise UnsupportedDocumentError(
            f"{name} has extension {extension!r} but its content looks like "
            f"{sniffed!r}; rename or re-export the file."
        )

    declared = (declared_media_type or "").strip().lower()
    if declared not in _GENERIC_MEDIA_TYPES and declared != sniffed:
        raise UnsupportedDocumentError(
            f"{name} declares {declared!r} but its content is {sniffed!r}; refusing the upload."
        )
    return name, extension, sniffed


def storage_root(data_directory: Path | str) -> Path:
    """Directory that holds every user's stored documents."""

    return Path(data_directory).expanduser() / "documents"


def _relative_storage_path(user_id: int, digest: str, extension: str) -> str:
    """Build the storage path from trusted values only.

    The client's file name never reaches the filesystem, so ``../`` traversal and
    symlink-style names are impossible.
    """

    return f"user_{user_id}/{digest[:32]}{extension}"


def _resolve(storage_path: str, data_directory: Path | str) -> Path:
    root = storage_root(data_directory).resolve()
    candidate = (root / storage_path).resolve()
    if not candidate.is_relative_to(root):
        # Only reachable if the database row was tampered with.
        raise DocumentError("stored document path escapes the data directory; refusing to read it.")
    return candidate


def _row_to_record(row: sqlite3.Row) -> DocumentRecord:
    return DocumentRecord(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        kind=row["kind"],
        display_name=row["display_name"],
        media_type=row["media_type"],
        extension=row["extension"],
        size_bytes=int(row["size_bytes"]),
        sha256=row["sha256"],
        storage_path=row["storage_path"],
        status=row["status"],
        parse_error=row["parse_error"],
        uploaded_at=row["uploaded_at"],
        parsed_at=row["parsed_at"],
    )


_COLUMNS = (
    "id, user_id, kind, display_name, media_type, extension, size_bytes, sha256, "
    "storage_path, status, parse_error, uploaded_at, parsed_at"
)


def _validate_kind(kind: str) -> str:
    if kind not in DOCUMENT_KINDS:
        raise DocumentError(f"kind must be one of {', '.join(DOCUMENT_KINDS)}; got {kind!r}.")
    return kind


def _find_by_hash(connection: sqlite3.Connection, user_id: int, digest: str) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT {_COLUMNS} FROM student_documents WHERE user_id = ? AND sha256 = ?",  # noqa: S608
        (user_id, digest),
    ).fetchone()


def save_document(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    display_name: str,
    payload: bytes,
    data_directory: Path | str,
    kind: str = "cv",
    declared_media_type: str | None = None,
    actor: str,
) -> DocumentRecord:
    """Validate, store and record one document for one user.

    Re-uploading identical bytes returns the existing record unchanged, so a
    retried upload neither duplicates the row nor the file.
    """

    kind = _validate_kind(kind)
    name, extension, media_type = validate_document(
        display_name, payload, declared_media_type=declared_media_type
    )

    if not connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
        raise DocumentError(f"user {user_id} does not exist; create the user first.")

    digest = hashlib.sha256(payload).hexdigest()
    existing = _find_by_hash(connection, user_id, digest)
    if existing is not None:
        record = _row_to_record(existing)
        logger.info("document re-upload ignored document_id=%s user_id=%s", record.id, user_id)
        return record

    relative_path = _relative_storage_path(user_id, digest, extension)
    target = _resolve(relative_path, data_directory)
    target.parent.mkdir(parents=True, exist_ok=True)

    created_file = False
    if not target.exists():
        temporary = target.with_name(target.name + ".part")
        try:
            temporary.write_bytes(payload)
            try:
                os.chmod(temporary, 0o600)
            except OSError:  # pragma: no cover - Windows without POSIX modes
                logger.debug("could not restrict document permissions on this platform")
            os.replace(temporary, target)
            created_file = True
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise DocumentError(f"could not store {name}: {exc.strerror or exc}") from exc

    now = db.utc_now()
    try:
        with db.transaction(connection):
            cursor = connection.execute(
                "INSERT INTO student_documents "
                "(user_id, kind, display_name, media_type, extension, size_bytes, sha256, "
                "storage_path, status, uploaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'stored', ?)",
                (
                    user_id,
                    kind,
                    name,
                    media_type,
                    extension,
                    len(payload),
                    digest,
                    relative_path,
                    now,
                ),
            )
            document_id = int(cursor.lastrowid or 0)
            db.record_audit_event(
                connection,
                actor=actor,
                action="document.uploaded",
                outcome="success",
                target_type="student_document",
                target_id=str(document_id),
                metadata={
                    "kind": kind,
                    "size_bytes": len(payload),
                    "media_type": media_type,
                    "sha256": digest,
                },
            )
    except sqlite3.IntegrityError:
        # Two workers stored the same bytes at the same time: the unique
        # (user_id, sha256) row won, so return it instead of raising.
        concurrent = _find_by_hash(connection, user_id, digest)
        if concurrent is None:  # pragma: no cover - uniqueness violated elsewhere
            raise
        return _row_to_record(concurrent)
    except BaseException:
        if created_file:
            target.unlink(missing_ok=True)
        raise
    logger.info(
        "document stored document_id=%s user_id=%s kind=%s size_bytes=%s",
        document_id,
        user_id,
        kind,
        len(payload),
    )
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM student_documents WHERE id = ?",
        (document_id,),  # noqa: S608
    ).fetchone()
    return _row_to_record(row)


def list_documents(
    connection: sqlite3.Connection, user_id: int, *, kind: str | None = None
) -> tuple[DocumentRecord, ...]:
    """Return the user's documents, newest first."""

    query = f"SELECT {_COLUMNS} FROM student_documents WHERE user_id = ?"  # noqa: S608
    params: list[Any] = [user_id]
    if kind is not None:
        query += " AND kind = ?"
        params.append(_validate_kind(kind))
    query += " ORDER BY uploaded_at DESC, id DESC"
    return tuple(_row_to_record(row) for row in connection.execute(query, params))


def get_document(
    connection: sqlite3.Connection, *, document_id: int, user_id: int
) -> DocumentRecord:
    """Return one document owned by ``user_id`` or raise :class:`DocumentNotFoundError`."""

    row = connection.execute(
        f"SELECT {_COLUMNS} FROM student_documents WHERE id = ? AND user_id = ?",  # noqa: S608
        (document_id, user_id),
    ).fetchone()
    if row is None:
        raise DocumentNotFoundError(f"document {document_id} does not exist for user {user_id}.")
    return _row_to_record(row)


def read_document(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    user_id: int,
    data_directory: Path | str,
) -> tuple[DocumentRecord, bytes]:
    """Read a document's bytes, owner-scoped."""

    record = get_document(connection, document_id=document_id, user_id=user_id)
    path = _resolve(record.storage_path, data_directory)
    try:
        payload = path.read_bytes()
    except FileNotFoundError as exc:
        raise DocumentError(
            f"the stored file for document {document_id} is missing; upload it again."
        ) from exc
    if hashlib.sha256(payload).hexdigest() != record.sha256:
        raise DocumentError(f"document {document_id} failed its integrity check; upload it again.")
    return record, payload


def mark_parsed(
    connection: sqlite3.Connection, *, document_id: int, user_id: int, actor: str
) -> bool:
    """Record a successful parse."""

    with db.transaction(connection):
        cursor = connection.execute(
            "UPDATE student_documents SET status = 'parsed', parse_error = NULL, parsed_at = ? "
            "WHERE id = ? AND user_id = ?",
            (db.utc_now(), document_id, user_id),
        )
        updated = cursor.rowcount > 0
        if updated:
            db.record_audit_event(
                connection,
                actor=actor,
                action="document.parsed",
                outcome="success",
                target_type="student_document",
                target_id=str(document_id),
            )
    return updated


def mark_parse_failed(
    connection: sqlite3.Connection, *, document_id: int, user_id: int, reason: str, actor: str
) -> bool:
    """Record a failed parse with a short, content-free reason."""

    message = " ".join(reason.split())[:300]
    with db.transaction(connection):
        cursor = connection.execute(
            "UPDATE student_documents SET status = 'failed', parse_error = ? "
            "WHERE id = ? AND user_id = ?",
            (message, document_id, user_id),
        )
        updated = cursor.rowcount > 0
        if updated:
            db.record_audit_event(
                connection,
                actor=actor,
                action="document.parse_failed",
                outcome="failure",
                target_type="student_document",
                target_id=str(document_id),
                metadata={"reason": message},
            )
    return updated


def purge_user_documents(
    connection: sqlite3.Connection, *, user_id: int, data_directory: Path | str, actor: str
) -> int:
    """Delete every document of one user: rows first, then files.

    Called by the account-deletion flow before the user row is removed, because
    SQLite cascades rows but knows nothing about files on disk. Returns the
    number of documents removed.
    """

    rows = connection.execute(
        "SELECT id, storage_path FROM student_documents WHERE user_id = ?", (user_id,)
    ).fetchall()
    if not rows:
        return 0

    with db.transaction(connection):
        connection.execute("DELETE FROM student_documents WHERE user_id = ?", (user_id,))
        db.record_audit_event(
            connection,
            actor=actor,
            action="document.purged",
            outcome="success",
            target_type="user",
            target_id=str(user_id),
            metadata={"documents": len(rows)},
        )

    for row in rows:
        try:
            _resolve(row["storage_path"], data_directory).unlink(missing_ok=True)
        except (OSError, DocumentError):  # pragma: no cover - locked/odd filesystem
            logger.warning("purged document row but file removal failed id=%s", row["id"])
    return len(rows)


def delete_document(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    user_id: int,
    data_directory: Path | str,
    actor: str,
) -> bool:
    """Delete one document (row + file). Returns ``False`` when it does not exist."""

    record = connection.execute(
        "SELECT storage_path FROM student_documents WHERE id = ? AND user_id = ?",
        (document_id, user_id),
    ).fetchone()
    if record is None:
        return False

    with db.transaction(connection):
        connection.execute(
            "DELETE FROM student_documents WHERE id = ? AND user_id = ?", (document_id, user_id)
        )
        db.record_audit_event(
            connection,
            actor=actor,
            action="document.deleted",
            outcome="success",
            target_type="student_document",
            target_id=str(document_id),
        )

    path = _resolve(record["storage_path"], data_directory)
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - filesystem race/lock
        logger.warning("document row deleted but file could not be removed id=%s", document_id)
    return True


def upload_batch(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    uploads: Sequence[tuple[str, bytes]],
    data_directory: Path | str,
    kind: str = "cv",
    actor: str,
) -> tuple[DocumentRecord, ...]:
    """Store several uploads, returning the records in input order."""

    return tuple(
        save_document(
            connection,
            user_id=user_id,
            display_name=name,
            payload=payload,
            data_directory=data_directory,
            kind=kind,
            actor=actor,
        )
        for name, payload in uploads
    )
