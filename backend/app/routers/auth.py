from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from app.db import get_session
from app.models import User
from app.schemas import LoginRequest
from app.security.audit import audit
from app.security.auth import COOKIE, TOKEN_HOURS, create_token, current_user, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def public_user(u: User) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "linked_elder_ids": u.linked_elder_ids,
    }


@router.post("/login")
def login(body: LoginRequest, response: Response, session: Session = Depends(get_session)) -> dict:
    user = session.exec(select(User).where(User.email == body.email.strip().lower())).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    response.set_cookie(
        COOKIE, create_token(user.id), httponly=True, samesite="lax", max_age=TOKEN_HOURS * 3600
    )
    audit(session, user.id, "login", "user", user.id)
    return public_user(user)


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return public_user(user)
