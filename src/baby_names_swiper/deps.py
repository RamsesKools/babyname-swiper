"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import Cookie, HTTPException, status
from itsdangerous import BadSignature, URLSafeSerializer

from baby_names_swiper.config import COOKIE_SECRET, USERS

_serializer = URLSafeSerializer(COOKIE_SECRET, salt="who")


def sign_user(user: str) -> str:
    return _serializer.dumps(user)


def read_user(token: str | None) -> str | None:
    if not token:
        return None
    try:
        value = _serializer.loads(token)
    except BadSignature:
        return None
    if not isinstance(value, str) or value not in USERS:
        return None
    return value


def current_user(who: str | None = Cookie(default=None)) -> str:
    """Return the cookie-authenticated user, or 401 if missing."""
    user = read_user(who)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Pick a user first",
            headers={"Location": "/who"},
        )
    return user
