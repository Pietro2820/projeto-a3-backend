import os
import json
import redis

STATUS_REDIS_URL = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

_redis_client = redis.Redis.from_url(STATUS_REDIS_URL, decode_responses=True)

STATUS_KEY_PREFIX = "video_status:"


def set_status(video_id: str, status: str, extra: dict | None = None):
    """status esperado: pending | processing | completed | failed"""
    data = {"video_id": video_id, "status": status}
    if extra:
        data.update(extra)
    _redis_client.set(f"{STATUS_KEY_PREFIX}{video_id}", json.dumps(data))


def get_status(video_id: str) -> dict | None:
    raw = _redis_client.get(f"{STATUS_KEY_PREFIX}{video_id}")
    if raw is None:
        return None
    return json.loads(raw)