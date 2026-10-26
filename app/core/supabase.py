from supabase import Client, create_client
from supabase.client import ClientOptions

from core.config import get_settings


def auth_client() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def user_client(access_token: str) -> Client:
    """Request-scoped client: Supabase RLS evaluates this user's JWT."""
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_anon_key,
        options=ClientOptions(
            headers={"Authorization": f"Bearer {access_token}"},
            persist_session=False,
            auto_refresh_token=False,
        ),
    )
