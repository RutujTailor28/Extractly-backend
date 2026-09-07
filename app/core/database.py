from typing import Optional

from supabase import Client, create_client

from app.core.config import settings

supabase: Optional[Client] = None
supabase_admin: Optional[Client] = None


def get_supabase_client() -> Optional[Client]:
    """
    Create and return a Supabase anon client.

    Returns None when credentials/URL are invalid so the API can still start
    and health checks can report disconnected without crashing.
    """
    global supabase

    if supabase is not None:
        return supabase

    try:
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    except Exception:
        supabase = None

    return supabase


def get_supabase_admin_client() -> Optional[Client]:
    """
    Create a server-side Supabase client.

    Prefers the service role key for Storage uploads and inserts. Falls back to
    the anon key when the service role key is not configured.
    Never expose the service role key to the frontend.
    """
    global supabase_admin

    if supabase_admin is not None:
        return supabase_admin

    key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY
    try:
        supabase_admin = create_client(settings.SUPABASE_URL, key)
    except Exception:
        supabase_admin = None

    return supabase_admin


# Eager init on import; failures are non-fatal.
get_supabase_client()
get_supabase_admin_client()
