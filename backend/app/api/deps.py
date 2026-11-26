"""FastAPI dependencies: app state, current user and rate limiting."""
from __future__ import annotations

import ipaddress
import threading
import time
from collections import defaultdict
from collections.abc import Iterator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.errors import ApiError, unauthorized
from ..core.security import decode_token
from ..db.models import User


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.db.session()
    try:
        yield session
    finally:
        session.close()


def get_registry(request: Request):
    return request.app.state.registry


def get_task_queue(request: Request):
    return request.app.state.tasks


class RateLimiter:
    MAX_KEYS = 10_000

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _prune(self, key: str, window: int) -> None:
        cutoff = time.time() - window
        self._hits[key] = [stamp for stamp in self._hits[key] if stamp >= cutoff]

    def retry_after(self, key: str, limit: int, window: int) -> int | None:
        with self._lock:
            self._prune(key, window)
            if len(self._hits) > self.MAX_KEYS:
                # Bound memory under IP/username spraying; limits reset briefly.
                self._hits.clear()
                self._prune(key, window)
            if len(self._hits[key]) >= limit:
                oldest = self._hits[key][0]
                return max(1, int(window - (time.time() - oldest)) + 1)
            return None

    def hit(self, key: str) -> None:
        with self._lock:
            self._hits[key].append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # The edge proxy must overwrite X-Forwarded-For. Taking the last
            # entry also defeats clients that append a spoofed value.
            parts = [part.strip() for part in forwarded.split(",") if part.strip()]
            if parts:
                try:
                    ipaddress.ip_address(parts[-1])
                    return parts[-1]
                except ValueError:
                    pass
    return request.client.host if request.client else "unknown"


def rate_limit_error(retry_after: int) -> ApiError:
    return ApiError(429, 42901, "请求过于频繁，请稍后重试", {"retry_after_seconds": retry_after})


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized()
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise unauthorized()
    user_id = decode_token(get_settings(request), token)
    user = db.get(User, user_id)
    if not user:
        raise unauthorized()
    return user