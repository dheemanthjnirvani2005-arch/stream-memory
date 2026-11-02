from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

COOKIE_NAME = "stream_memory_session"


def _serializer() -> URLSafeTimedSerializer:
    secret = get_settings().session_secret.get_secret_value()
    if not secret:
        raise RuntimeError("SESSION_SECRET must be configured")
    return URLSafeTimedSerializer(secret, salt="stream-memory-session")


def make_session(broadcaster_id: str) -> str:
    return _serializer().dumps({"broadcaster_id": broadcaster_id})


def read_session(value: str | None) -> str | None:
    if not value:
        return None
    try:
        payload = _serializer().loads(value, max_age=60 * 60 * 24 * 30)
    except (BadSignature, SignatureExpired, RuntimeError):
        return None
    broadcaster_id = payload.get("broadcaster_id") if isinstance(payload, dict) else None
    return broadcaster_id if isinstance(broadcaster_id, str) else None
