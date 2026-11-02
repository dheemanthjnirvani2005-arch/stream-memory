from functools import lru_cache
import logging

from supabase import Client, create_client

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_supabase_client() -> Client | None:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key.get_secret_value():
        return None
    return create_client(settings.supabase_url, settings.supabase_secret_key.get_secret_value())


def check_database_health() -> bool:
    client = get_supabase_client()
    if client is None:
        return False
    client.table("communities").select("id").limit(1).execute()
    return True


def upsert_community(broadcaster: dict[str, str]) -> dict[str, object]:
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase is not configured")
    result = client.table("communities").upsert(
        {
            "twitch_broadcaster_id": broadcaster["twitch_broadcaster_id"],
            "twitch_login": broadcaster["twitch_login"],
            "display_name": broadcaster["display_name"],
        },
        on_conflict="twitch_broadcaster_id",
    ).execute()
    if not result.data:
        raise RuntimeError("Twitch community could not be saved")
    return result.data[0]


def save_twitch_credentials(credentials: dict[str, object]) -> dict[str, object]:
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase is not configured")
    fields = (
        "community_id", "twitch_user_id", "access_token_encrypted",
        "refresh_token_encrypted", "scopes", "expires_at",
    )
    result = client.table("twitch_connections").upsert(
        {field: credentials[field] for field in fields}, on_conflict="community_id"
    ).execute()
    if not result.data:
        raise RuntimeError("Twitch credentials could not be saved")
    return result.data[0]


def load_twitch_connection(broadcaster_id: str) -> dict[str, object] | None:
    client = get_supabase_client()
    if client is None:
        return None
    community_result = (client.table("communities")
                        .select("id,twitch_broadcaster_id,twitch_login,display_name")
                        .eq("twitch_broadcaster_id", broadcaster_id).limit(1).execute())
    if not community_result.data:
        return None
    community = community_result.data[0]
    connection_result = (client.table("twitch_connections").select("*")
                         .eq("community_id", community["id"]).limit(1).execute())
    if not connection_result.data:
        return None
    return {**community, **connection_result.data[0]}


def disconnect_twitch_connection(broadcaster_id: str) -> bool:
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase is not configured")
    community_id = community_id_for_broadcaster(broadcaster_id)
    result = (client.table("twitch_connections").delete()
              .eq("community_id", community_id).execute())
    return bool(result.data)


def update_twitch_tokens(broadcaster_id: str, old_refresh_token: str,
                         access_token: str, refresh_token: str,
                         expires_at: str) -> None:
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase is not configured")
    community_id = community_id_for_broadcaster(broadcaster_id)
    result = (client.table("twitch_connections").update({
        "access_token_encrypted": access_token,
        "refresh_token_encrypted": refresh_token,
        "expires_at": expires_at,
    }).eq("community_id", community_id)
      .eq("refresh_token_encrypted", old_refresh_token).execute())
    if not result.data:
        raise RuntimeError("Twitch token rotation conflict; reload credentials")


def community_id_for_broadcaster(broadcaster_id: str) -> str:
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase is not configured")
    result = (client.table("communities").select("id")
              .eq("twitch_broadcaster_id", broadcaster_id).limit(1).execute())
    if not result.data:
        raise LookupError("Connected Twitch community was not found")
    return str(result.data[0]["id"])


def create_memory(broadcaster_id: str, payload: dict[str, object], source: dict[str, object] | None) -> dict[str, object]:
    client = get_supabase_client()
    community_id = community_id_for_broadcaster(broadcaster_id)
    inserted = client.table("memories").insert({**payload, "community_id": community_id}).execute()
    if not inserted.data:
        raise RuntimeError("Memory could not be created")
    memory = inserted.data[0]
    if source:
        client.table("memory_sources").insert({**source, "memory_id": memory["id"]}).execute()
    return memory


def create_memory_with_sources(broadcaster_id: str, payload: dict[str, object], sources: list[dict[str, object]]) -> dict[str, object]:
    client = get_supabase_client()
    community_id = community_id_for_broadcaster(broadcaster_id)
    inserted = client.table("memories").insert({**payload, "community_id": community_id}).execute()
    if not inserted.data:
        raise RuntimeError("Memory could not be created")
    memory = inserted.data[0]
    if sources:
        client.table("memory_sources").insert([{**source, "memory_id": memory["id"]} for source in sources]).execute()
    return memory


def list_memories(broadcaster_id: str, status: str | None = None, query: str | None = None) -> list[dict[str, object]]:
    client = get_supabase_client()
    community_id = community_id_for_broadcaster(broadcaster_id)
    request = client.table("memories").select("*,memory_sources(*)").eq("community_id", community_id)
    if status:
        request = request.eq("status", status)
    if query:
        request = request.or_(f"title.ilike.%{query}%,description.ilike.%{query}%")
    return request.order("created_at", desc=True).execute().data or []


def update_memory(broadcaster_id: str, memory_id: str, changes: dict[str, object]) -> dict[str, object] | None:
    client = get_supabase_client()
    community_id = community_id_for_broadcaster(broadcaster_id)
    result = (client.table("memories").update(changes).eq("id", memory_id)
              .eq("community_id", community_id).execute())
    return result.data[0] if result.data else None


def delete_memory(broadcaster_id: str, memory_id: str) -> bool:
    client = get_supabase_client()
    community_id = community_id_for_broadcaster(broadcaster_id)
    result = (client.table("memories").delete().eq("id", memory_id)
              .eq("community_id", community_id).execute())
    return bool(result.data)


def search_approved_memories(broadcaster_id: str, query: str, limit: int = 5) -> list[dict[str, object]]:
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase is not configured")
    logger.info("lore_trace stage=community_resolution result=delegated broadcaster_id=%s",
                broadcaster_id)
    result = client.rpc("search_approved_memories", {
        "p_broadcaster_id": broadcaster_id,
        "p_query": query,
        "p_limit": min(max(limit, 1), 10),
    }).execute()
    memories = result.data or []
    logger.info("lore_trace stage=approved_memory_rpc result=success record_count=%d",
                len(memories))
    return memories
