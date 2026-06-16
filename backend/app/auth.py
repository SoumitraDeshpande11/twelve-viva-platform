from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError


STAFF_SESSION_HOURS = int(os.getenv("TWELVE_STAFF_SESSION_HOURS", "12"))
STUDENT_SESSION_HOURS = int(os.getenv("TWELVE_STUDENT_SESSION_HOURS", "4"))
AUTH_COOKIE = "twelve_session"
CSRF_COOKIE = "twelve_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_password_hasher = PasswordHasher()

_LOCAL_ENVS = {"local", "development", "test"}
_DEV_SECRET_FALLBACK = "twelve-local-dev-secret"


def secret_key() -> bytes:
    value = os.getenv("TWELVE_SECRET_KEY") or os.getenv("TWELVE_TOKEN_PEPPER")
    if not value:
        env = os.getenv("TWELVE_ENV", "local").lower()
        if env not in _LOCAL_ENVS:
            raise RuntimeError(
                "TWELVE_SECRET_KEY (or TWELVE_TOKEN_PEPPER) must be set when "
                f"TWELVE_ENV={env!r}. Refusing to start with the insecure dev fallback "
                "secret in a non-local deployment."
            )
        value = _DEV_SECRET_FALLBACK
    return value.encode("utf-8")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def hash_opaque_token(token: str) -> str:
    return hmac.new(secret_key(), token.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_token(byte_count: int = 32) -> str:
    return secrets.token_urlsafe(byte_count)


def expires_at(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def is_expired(value: str | None) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def session_cookie_options() -> dict[str, Any]:
    secure_default = os.getenv("TWELVE_ENV", "local").lower() not in {"local", "development", "test"}
    secure = os.getenv("TWELVE_COOKIE_SECURE", str(secure_default)).lower() in {"1", "true", "yes"}
    return {"httponly": True, "secure": secure, "samesite": "lax", "path": "/"}
