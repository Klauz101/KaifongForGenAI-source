"""Password hashing, session tokens and role-based access."""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from app.config import get_settings
from app.db import get_session
from app.models import Elder, User

COOKIE = "kf_session"
TOKEN_HOURS = 12


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user_id: int) -> str:
    exp = datetime.now(UTC) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": str(user_id), "exp": exp}, get_settings().secret_key, "HS256")


def _token_from(request: Request) -> str | None:
    bearer = request.headers.get("authorization", "")
    if bearer.lower().startswith("bearer "):
        return bearer[7:]
    return request.cookies.get(COOKIE)


def current_user(request: Request, session: Session = Depends(get_session)) -> User:
    token = _token_from(request)
    if not token:
        raise HTTPException(401, "Not signed in")
    try:
        user_id = int(jwt.decode(token, get_settings().secret_key, ["HS256"])["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Invalid or expired session") from None
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(401, "Unknown user")
    return user


def require_staff(user: User = Depends(current_user)) -> User:
    """Admins and social workers only; family accounts never reach transcripts/alerts."""
    if user.role not in ("admin", "social_worker"):
        raise HTTPException(403, "Not permitted for this account type")
    return user


def check_elder_access(user: User, elder: Elder) -> None:
    if user.role == "admin":
        return
    if user.role == "social_worker" and elder.caseworker_id == user.id:
        return
    raise HTTPException(403, "Not permitted for this person")


def caseload_ids(user: User, session: Session) -> list[int] | None:
    """Elder ids a staff user may see; None means all (admin)."""
    from sqlmodel import select

    if user.role == "admin":
        return None
    return list(session.exec(select(Elder.id).where(Elder.caseworker_id == user.id)))
