"""Lightweight Supabase connectivity checks for health and dev status endpoints."""

from __future__ import annotations

from typing import Literal

from app.core.database import get_supabase_client

DatabaseStatus = Literal["connected", "disconnected"]


def check_database_connection() -> DatabaseStatus:
    """
    Verify Supabase PostgREST connectivity without raising.

    Attempts a minimal read against the documents table. An empty result set
    still counts as connected. Missing tables, invalid credentials, or network
    failures return disconnected so /health never crashes the server.
    """
    client = get_supabase_client()
    if client is None:
        return "disconnected"

    try:
        client.table("documents").select("id").limit(1).execute()
        return "connected"
    except Exception:
        return "disconnected"
