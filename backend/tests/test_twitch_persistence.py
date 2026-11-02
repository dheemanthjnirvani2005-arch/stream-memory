from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.database import supabase
from app.main import app
from app.routes import twitch_auth
from app.twitch import runtime


class FakeQuery:
    def __init__(self, client: "FakeSupabase", table: str) -> None:
        self.client = client
        self.table_name = table
        self.operation = "select"
        self.payload: Any = None
        self.conflict: str | None = None
        self.filters: list[tuple[str, Any]] = []

    def select(self, _columns: str) -> "FakeQuery":
        self.operation = "select"
        return self

    def upsert(self, payload: Any, on_conflict: str) -> "FakeQuery":
        self.operation, self.payload, self.conflict = "upsert", payload, on_conflict
        self.client.writes.append((self.table_name, self.operation, payload, on_conflict))
        return self

    def update(self, payload: Any) -> "FakeQuery":
        self.operation, self.payload = "update", payload
        self.client.writes.append((self.table_name, self.operation, payload, None))
        return self

    def delete(self) -> "FakeQuery":
        self.operation = "delete"
        self.client.writes.append((self.table_name, self.operation, None, None))
        return self

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self.filters.append((column, value))
        return self

    def limit(self, _limit: int) -> "FakeQuery":
        return self

    def execute(self) -> SimpleNamespace:
        self.client.queries.append((self.table_name, self.operation, list(self.filters)))
        key = (self.table_name, self.operation)
        rows = self.client.responses.get(key, [])
        if callable(rows):
            rows = rows(self)
        return SimpleNamespace(data=rows)


class FakeSupabase:
    def __init__(self, responses: dict[tuple[str, str], Any] | None = None) -> None:
        self.responses = responses or {}
        self.queries: list[tuple[str, str, list[tuple[str, Any]]]] = []
        self.writes: list[tuple[str, str, Any, str | None]] = []

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)


@pytest.fixture
def use_supabase(monkeypatch: pytest.MonkeyPatch):
    def install(client: FakeSupabase) -> FakeSupabase:
        monkeypatch.setattr(supabase, "get_supabase_client", lambda: client)
        return client
    return install


@pytest.mark.parametrize("existing", [False, True], ids=["new-community", "existing-community"])
def test_community_upsert_contains_only_identity_columns(use_supabase, existing: bool) -> None:
    community_id = "community-existing" if existing else "community-new"
    db = use_supabase(FakeSupabase({("communities", "upsert"): [{"id": community_id}]}))

    community = supabase.upsert_community({
        "twitch_broadcaster_id": "broadcaster-1",
        "twitch_login": "streamer",
        "display_name": "Streamer",
        "access_token_encrypted": "must-not-be-written",
    })

    assert community["id"] == community_id
    assert db.writes == [(
        "communities", "upsert", {
            "twitch_broadcaster_id": "broadcaster-1",
            "twitch_login": "streamer",
            "display_name": "Streamer",
        }, "twitch_broadcaster_id",
    )]


@pytest.mark.parametrize("reconnect", [False, True], ids=["insert", "reconnect-update"])
def test_credentials_upsert_uses_community_uuid_and_schema_columns(use_supabase, reconnect: bool) -> None:
    db = use_supabase(FakeSupabase({("twitch_connections", "upsert"): [{"community_id": "community-1"}]}))
    credentials = {
        "community_id": "community-1", "twitch_user_id": "broadcaster-1",
        "access_token_encrypted": "encrypted-access", "refresh_token_encrypted": "encrypted-refresh",
        "scopes": ["user:read:chat"], "expires_at": "2026-11-02T12:00:00+00:00",
        "token_expires_at": "must-not-be-written",
    }

    saved = supabase.save_twitch_credentials(credentials)

    assert saved["community_id"] == "community-1"
    table, operation, payload, conflict = db.writes[0]
    assert (table, operation, conflict) == ("twitch_connections", "upsert", "community_id")
    assert payload == {key: credentials[key] for key in (
        "community_id", "twitch_user_id", "access_token_encrypted",
        "refresh_token_encrypted", "scopes", "expires_at",
    )}


def test_load_credentials_resolves_community_then_foreign_key(use_supabase) -> None:
    db = use_supabase(FakeSupabase({
        ("communities", "select"): [{"id": "community-1", "twitch_broadcaster_id": "broadcaster-1",
                                      "display_name": "Streamer"}],
        ("twitch_connections", "select"): [{"community_id": "community-1", "twitch_user_id": "broadcaster-1",
                                              "access_token_encrypted": "cipher"}],
    }))

    result = supabase.load_twitch_connection("broadcaster-1")

    assert result is not None
    assert result["community_id"] == "community-1"
    assert result["display_name"] == "Streamer"
    assert db.queries[0][0:2] == ("communities", "select")
    assert db.queries[0][2] == [("twitch_broadcaster_id", "broadcaster-1")]
    assert db.queries[1][0:2] == ("twitch_connections", "select")
    assert db.queries[1][2] == [("community_id", "community-1")]


def test_rotated_credentials_persist_to_matching_connection(use_supabase) -> None:
    db = use_supabase(FakeSupabase({
        ("communities", "select"): [{"id": "community-1"}],
        ("twitch_connections", "update"): [{"community_id": "community-1"}],
    }))

    supabase.update_twitch_tokens(
        "broadcaster-1", "old-refresh", "new-access", "new-refresh", "2026-11-02T12:00:00+00:00"
    )

    assert db.writes == [(
        "twitch_connections", "update",
        {"access_token_encrypted": "new-access", "refresh_token_encrypted": "new-refresh",
         "expires_at": "2026-11-02T12:00:00+00:00"}, None,
    )]
    assert db.queries[-1][2] == [
        ("community_id", "community-1"), ("refresh_token_encrypted", "old-refresh")
    ]


def test_disconnect_deletes_only_connection_record(use_supabase) -> None:
    db = use_supabase(FakeSupabase({
        ("communities", "select"): [{"id": "community-1"}],
        ("twitch_connections", "delete"): [{"community_id": "community-1"}],
    }))

    assert supabase.disconnect_twitch_connection("broadcaster-1") is True
    assert db.writes == [("twitch_connections", "delete", None, None)]
    assert all(table != "communities" and table != "memories" and table != "memory_sources"
               for table, _, _, _ in db.writes)


@pytest.mark.asyncio
async def test_refresh_rotates_persisted_encrypted_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from app.twitch.service import TwitchAuthenticationError

    async def invalid(_token: str) -> dict[str, Any]:
        if _token == "plain:old-access":
            raise TwitchAuthenticationError(401, "Unauthorized", "Expired")
        return {"client_id": "test-client", "user_id": "broadcaster-1",
                "scopes": ["user:read:chat", "user:write:chat"], "expires_in": 3600}

    async def refreshed(_token: str) -> dict[str, str]:
        return {"access_token": "new-access", "refresh_token": "new-refresh"}

    saved: list[tuple[Any, ...]] = []
    monkeypatch.setattr(runtime.twitch_service, "validate_token", invalid)
    monkeypatch.setattr(runtime, "settings", SimpleNamespace(twitch_client_id="test-client"))
    monkeypatch.setattr(runtime.twitch_service, "refresh", refreshed)
    monkeypatch.setattr(runtime, "decrypt_token", lambda token: f"plain:{token}")
    monkeypatch.setattr(runtime, "encrypt_token", lambda token: f"encrypted:{token}")
    monkeypatch.setattr(runtime, "update_twitch_tokens", lambda *args: saved.append(args))
    runtime._refresh_locks.clear()

    token = await runtime.refresh_connection({
        "twitch_broadcaster_id": "broadcaster-1", "access_token_encrypted": "old-access",
        "refresh_token_encrypted": "old-refresh",
    })

    assert token == "new-access"
    assert saved[0][:4] == (
        "broadcaster-1", "old-refresh", "encrypted:new-access", "encrypted:new-refresh"
    )
    assert isinstance(saved[0][4], str)


def test_database_failure_does_not_issue_session_or_log_credentials(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    secrets = {"access_token": "ACCESS_SENTINEL", "refresh_token": "REFRESH_SENTINEL"}
    async def exchange(_code: str) -> dict[str, Any]:
        return {**secrets, "expires_in": 3600}
    async def validate(_token: str) -> dict[str, Any]:
        return {"user_id": "broadcaster-1", "scopes": ["user:read:chat"]}
    async def broadcaster(_token: str) -> dict[str, str]:
        return {"id": "broadcaster-1", "login": "streamer", "display_name": "Streamer"}

    monkeypatch.setattr(twitch_auth.twitch_service, "exchange_code", exchange)
    monkeypatch.setattr(twitch_auth.twitch_service, "validate_token", validate)
    monkeypatch.setattr(twitch_auth.twitch_service, "broadcaster", broadcaster)
    monkeypatch.setattr(twitch_auth, "upsert_community", lambda _data: {"id": "community-1"})
    monkeypatch.setattr(twitch_auth, "save_twitch_credentials",
                        lambda _data: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    monkeypatch.setattr(twitch_auth, "encrypt_token", lambda value: f"encrypted:{value}")
    make_session_calls: list[str] = []
    monkeypatch.setattr(twitch_auth, "make_session", lambda value: make_session_calls.append(value))

    client = TestClient(app)
    client.cookies.set("twitch_oauth_state", "valid")
    response = client.get(
        "/auth/twitch/callback?code=code&state=valid", follow_redirects=False
    )

    assert response.status_code == 502
    assert make_session_calls == []
    assert all(secret not in caplog.text for secret in secrets.values())

