from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    backend_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"
    session_secret: SecretStr = SecretStr("")
    token_encryption_key: SecretStr = SecretStr("")
    twitch_client_id: str = ""
    twitch_client_secret: SecretStr = SecretStr("")
    twitch_redirect_uri: str = "http://localhost:8000/auth/twitch/callback"
    twitch_scopes: str = "user:read:chat user:write:chat"
    twitch_success_redirect: str = "http://localhost:5173"
    session_cookie_secure: bool = False
    supabase_url: str = ""
    supabase_secret_key: SecretStr = SecretStr("")
    ai_provider: str = "mock"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""
    lore_channel_cooldown_seconds: int = 30
    lore_user_cooldown_seconds: int = 60

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
