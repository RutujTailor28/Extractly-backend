"""File validation and hashing utilities for document uploads."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from app.core.config import settings

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
}

EXTENSION_MIME_MAP = {
    ".pdf": {"application/pdf"},
    ".jpg": {"image/jpeg", "image/jpg"},
    ".jpeg": {"image/jpeg", "image/jpg"},
    ".png": {"image/png"},
}

EXTENSION_CANONICAL_MIME = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

# Clients like Postman often send this generic MIME for binary files
GENERIC_MIME_TYPES = {
    "",
    "application/octet-stream",
    "binary/octet-stream",
}


class FileValidationError(Exception):
    """Raised when an uploaded file fails validation."""

    def __init__(self, message: str, *, code: str = "INVALID_FILE") -> None:
        self.message = message
        self.code = code
        super().__init__(message)


def sanitize_filename(filename: str | None) -> str:
    """Return a safe basename without path components or traversal sequences."""
    if not filename or not filename.strip():
        raise FileValidationError("Filename is required", code="EMPTY_FILENAME")

    name = filename.replace("\\", "/").strip()
    # Drop any directory components (path traversal / absolute paths)
    name = PurePosixPath(name).name

    if not name or name in {".", ".."}:
        raise FileValidationError("Invalid filename", code="INVALID_FILENAME")

    # Remove control characters and normalize whitespace
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"\s+", "_", name)

    if not name or ".." in name:
        raise FileValidationError("Invalid filename", code="INVALID_FILENAME")

    return name


def get_file_extension(filename: str) -> str:
    """Return lowercase file extension including the leading dot."""
    return PurePosixPath(filename).suffix.lower()


def validate_extension(filename: str) -> str:
    """Validate and return the file extension."""
    extension = get_file_extension(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise FileValidationError(
            f"Unsupported file extension '{extension}'. "
            "Allowed: .pdf, .jpg, .jpeg, .png",
            code="UNSUPPORTED_EXTENSION",
        )
    return extension


def normalize_mime_type(mime_type: str | None) -> str:
    if not mime_type:
        return ""
    return mime_type.split(";")[0].strip().lower()


def validate_mime_type(mime_type: str | None, extension: str) -> str:
    """
    Validate MIME type and ensure it matches the file extension.

    If the client sends a missing/generic MIME (common with Postman),
    infer the MIME from the already-validated extension.
    """
    normalized = normalize_mime_type(mime_type)

    if normalized in GENERIC_MIME_TYPES:
        inferred = EXTENSION_CANONICAL_MIME.get(extension)
        if not inferred:
            raise FileValidationError(
                f"Unsupported file extension '{extension}'. "
                "Allowed: .pdf, .jpg, .jpeg, .png",
                code="UNSUPPORTED_EXTENSION",
            )
        return inferred

    if normalized not in ALLOWED_MIME_TYPES:
        raise FileValidationError(
            f"Unsupported file type '{normalized}'. "
            "Allowed: application/pdf, image/jpeg, image/png",
            code="UNSUPPORTED_MIME_TYPE",
        )

    allowed_for_ext = EXTENSION_MIME_MAP.get(extension, set())
    if normalized not in allowed_for_ext:
        raise FileValidationError(
            f"MIME type '{normalized}' does not match extension '{extension}'",
            code="MIME_EXTENSION_MISMATCH",
        )

    # Canonical MIME for storage/DB
    if normalized == "image/jpg":
        return "image/jpeg"
    return normalized


def validate_file_size(file_size: int, max_bytes: int | None = None) -> None:
    """Reject files that exceed the configured maximum size."""
    limit = max_bytes if max_bytes is not None else settings.max_file_size_bytes
    if file_size <= 0:
        raise FileValidationError("File is empty", code="EMPTY_FILE")
    if file_size > limit:
        max_mb = limit / (1024 * 1024)
        raise FileValidationError(
            f"File exceeds maximum allowed size of {max_mb:g} MB",
            code="FILE_TOO_LARGE",
        )


def compute_sha256(content: bytes) -> str:
    """Generate a SHA-256 hex digest from file bytes."""
    return hashlib.sha256(content).hexdigest()


def build_storage_path(document_id: str, safe_filename: str) -> str:
    """Build the object path inside the documents bucket."""
    return f"{document_id}/{safe_filename}"
