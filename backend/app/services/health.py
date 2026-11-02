import logging

from app.database.supabase import check_database_health

logger = logging.getLogger(__name__)


def get_health_status() -> tuple[str, bool]:
    try:
        database_ok = check_database_health()
    except Exception:
        logger.exception("Database health check failed")
        database_ok = False
    return ("ok" if database_ok else "degraded", database_ok)
