"""Fixed-window rate limiter. Redis-backed when REDIS_URL is set, in-process otherwise (single API process)."""

import logging
import threading
import time

from app.core.config import get_settings

log = logging.getLogger(__name__)
_lock = threading.Lock()
_memory: dict[str, tuple[int, float]] = {}
_redis = None


def _client():
    global _redis
    url = get_settings().redis_url
    if not url:
        return None
    if _redis is None:
        import redis

        _redis = redis.Redis.from_url(url)
    return _redis


def set_client(client) -> None:
    """Tests inject fakeredis here."""
    global _redis
    _redis = client


def hit(key: str, limit: int, window_seconds: int = 60) -> bool:
    """Record one hit; return True if still within the limit."""
    bucket = int(time.time() // window_seconds)
    full_key = f"rl:{key}:{bucket}"
    client = _client()
    if client is not None:
        try:
            pipe = client.pipeline()
            pipe.incr(full_key)
            pipe.expire(full_key, window_seconds + 5)
            count = pipe.execute()[0]
            return int(count) <= limit
        except Exception:
            # fail open to memory limiter rather than locking everyone out
            log.warning("redis rate limiter unavailable, using in-process fallback", exc_info=True)
    with _lock:
        now = time.time()
        for k in [k for k, (_, exp) in _memory.items() if exp < now]:
            _memory.pop(k, None)
        count, exp = _memory.get(full_key, (0, now + window_seconds))
        count += 1
        _memory[full_key] = (count, exp)
        return count <= limit


def reset() -> None:
    with _lock:
        _memory.clear()
