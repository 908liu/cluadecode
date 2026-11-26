"""Passwords, JWT and API-key encryption."""
from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import Settings
from .errors import unauthorized
from .utils import utcnow


def _prehash(password: str) -> bytes:
    # bcrypt has a 72-byte input limit; pre-hash so 128-char passwords work.
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=rounds)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_token(settings: Settings, user_id: str) -> tuple[str, int]:
    now = utcnow()
    expires_in = settings.token_ttl_seconds
    token = jwt.encode(
        {"sub": user_id, "iat": int(now.timestamp()), "exp": int((now + timedelta(seconds=expires_in)).timestamp())},
        settings.app_secret,
        algorithm="HS256",
    )
    return token, expires_in


def decode_token(settings: Settings, token: str) -> str:
    try:
        payload = jwt.decode(token, settings.app_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("登录已失效，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise unauthorized("登录已失效，请重新登录") from exc
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise unauthorized()
    return user_id


def ensure_encryption_key(settings: Settings) -> None:
    path = settings.encryption_key_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.write_bytes(Fernet.generate_key())
    try:
        path.chmod(0o600)
    except OSError:
        # Windows ACLs differ; best effort only.
        pass


@lru_cache(maxsize=8)
def _fernet_for(path: str) -> Fernet:
    return Fernet(Path(path).read_bytes())


def encrypt_key(settings: Settings, api_key: str) -> str:
    ensure_encryption_key(settings)
    return _fernet_for(str(settings.encryption_key_path)).encrypt(api_key.encode("utf-8")).decode("ascii")


def decrypt_key(settings: Settings, cipher: str) -> str:
    try:
        return _fernet_for(str(settings.encryption_key_path)).decrypt(cipher.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, OSError):
        return ""


def mask_key(api_key: str) -> str:
    if not api_key or len(api_key) < 8:
        return "****"
    return f"{api_key[:3]}****{api_key[-4:]}"