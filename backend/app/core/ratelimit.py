"""Rate limiting (SPECS §10.7, CA-06): Redis em prod, memória em dev/testes.

Janela fixa: hit(key, limit, window_s) → (allowed, retry_after_s).
429 padronizado via RateLimited + handler no main (Retry-After).
"""
import os
import time
from typing import Optional

from fastapi import Request


class RateLimited(Exception):
    def __init__(self, retry_after: int = 60):
        self.retry_after = max(1, int(retry_after))
        super().__init__(f"limite excedido; tente em {self.retry_after}s")


class Limiter:
    def __init__(self, memory_only: bool = False):
        self._memory_only = memory_only
        self._redis = None
        self._redis_ok: Optional[bool] = None
        self._mem: dict[str, list[float]] = {}

    def _client(self):
        if self._memory_only:
            return None
        if self._redis_ok is False:
            return None
        if self._redis is None:
            try:
                import redis as _redis

                client = _redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                                               socket_connect_timeout=2, socket_timeout=2)
                client.ping()
                self._redis = client
                self._redis_ok = True
            except Exception:
                self._redis_ok = False
                return None
        return self._redis

    def hit(self, key: str, limit: int, window_s: int) -> tuple[bool, int]:
        client = self._client()
        if client is not None:
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = pipe.execute()
            if count == 1:
                client.expire(key, window_s)
                ttl = window_s
            if count <= limit:
                return True, 0
            return False, max(1, int(ttl if ttl and ttl > 0 else window_s))
        now = time.monotonic()
        entry = self._mem.get(key)
        if entry is None or now >= entry[1]:
            entry = [0.0, now + window_s]
            self._mem[key] = entry
        entry[0] += 1
        if entry[0] <= limit:
            return True, 0
        return False, max(1, int(entry[1] - now))

    def reset(self) -> None:
        self._mem.clear()


_limiter: Limiter | None = None


def get_limiter() -> Limiter:
    global _limiter
    if _limiter is None:
        _limiter = Limiter()
    return _limiter


def _user_sub(request: Request) -> str:
    from app.core.security import decode_token
    from app.core.config import get_settings

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        sub = decode_token(auth[7:].strip(), get_settings().JWT_SECRET, expect="access")
        if sub:
            return f"u:{sub}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


async def _login_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    try:
        body = await request.json()
        email = str((body or {}).get("email", "")).strip().lower()
    except Exception:
        email = ""
    return f"login:{host}:{email}"


def limit(times: int, window_s: int, key: str = "user", prefix: str = "rl"):
    """Dependency factory. key: user | ip | login."""
    async def _dep(request: Request) -> None:
        if key == "login":
            resolved = await _login_key(request)
        elif key == "ip":
            host = request.client.host if request.client else "unknown"
            resolved = f"ip:{host}"
        else:
            resolved = _user_sub(request)
        allowed, retry = get_limiter().hit(f"{prefix}:{resolved}", times, window_s)
        if not allowed:
            raise RateLimited(retry)

    return _dep


def global_limit() -> tuple[int, int]:
    try:
        per_min = max(1, int(os.environ.get("RATE_GLOBAL_PER_MIN", "300")))
    except ValueError:
        per_min = 300
    return per_min, 60
