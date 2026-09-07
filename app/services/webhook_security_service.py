"""Webhook URL SSRF protection."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from app.core.config import settings
from app.services.document_service import DocumentServiceError

logger = logging.getLogger(__name__)

BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.google",
    "169.254.169.254",
}


def validate_webhook_url(url: str) -> str:
    """
    Validate webhook URL for SSRF safety.
    Returns normalized URL or raises DocumentServiceError.
    """
    if not url or not url.strip():
        raise DocumentServiceError(
            "Invalid webhook URL",
            status_code=422,
            code="INVALID_WEBHOOK_URL",
        )
    url = url.strip()
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").lower()

    if scheme not in {"https", "http"}:
        raise DocumentServiceError(
            "Invalid webhook URL",
            status_code=422,
            code="INVALID_WEBHOOK_URL",
        )

    is_local = hostname in {"localhost", "127.0.0.1", "::1"}
    if scheme == "http" and not (settings.WEBHOOK_ALLOW_LOCALHOST and is_local):
        raise DocumentServiceError(
            "Webhook URL must use HTTPS",
            status_code=422,
            code="INVALID_WEBHOOK_URL",
        )

    if not hostname:
        raise DocumentServiceError(
            "Invalid webhook URL",
            status_code=422,
            code="INVALID_WEBHOOK_URL",
        )

    if hostname in BLOCKED_HOSTNAMES:
        raise DocumentServiceError(
            "Invalid webhook URL",
            status_code=422,
            code="INVALID_WEBHOOK_URL",
        )

    if settings.ENVIRONMENT == "production" and is_local:
        raise DocumentServiceError(
            "Invalid webhook URL",
            status_code=422,
            code="INVALID_WEBHOOK_URL",
        )

    # Resolve hostname and block private / reserved ranges
    try:
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if scheme == "https" else 80))
    except socket.gaierror as exc:
        raise DocumentServiceError(
            "Invalid webhook URL",
            status_code=422,
            code="INVALID_WEBHOOK_URL",
        ) from exc

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            # Allow localhost only when configured for development
            if settings.WEBHOOK_ALLOW_LOCALHOST and settings.ENVIRONMENT != "production" and ip.is_loopback:
                continue
            raise DocumentServiceError(
                "Invalid webhook URL",
                status_code=422,
                code="INVALID_WEBHOOK_URL",
            )

    return url
