from fastapi.testclient import TestClient

from app.main import app
from app.routes import twitch_auth


def test_disconnect_stops_listener_and_preserves_community(monkeypatch) -> None:
    disconnected = []
    stopped = []

    class Listener:
        def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(twitch_auth, "read_session", lambda value: "broadcaster-1")
    monkeypatch.setattr(twitch_auth, "disconnect_twitch_connection",
                        lambda broadcaster_id: disconnected.append(broadcaster_id) or True)
    twitch_auth.listeners["broadcaster-1"] = Listener()
    response = TestClient(app).post("/auth/twitch/disconnect")
    assert response.status_code == 204
    assert stopped == [True]
    assert disconnected == ["broadcaster-1"]
    assert "broadcaster-1" not in twitch_auth.listeners
    assert "stream_memory_session" in response.headers.get("set-cookie", "")
