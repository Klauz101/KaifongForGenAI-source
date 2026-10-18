"""WebSockets: `/ws/calls/{call_id}` (live call) and `/ws/dashboard` (staff events)."""

import asyncio
import base64
import binascii
import logging

import jwt
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from app.config import get_settings
from app.conversation import service
from app.db import engine
from app.models import Call, Elder, User
from app.realtime.hub import hub
from app.security.auth import COOKIE, check_elder_access

logger = logging.getLogger(__name__)
router = APIRouter()


def _staff_from_cookie(ws: WebSocket, db: Session) -> User | None:
    token = ws.cookies.get(COOKIE)
    if not token:
        return None
    try:
        user = db.get(User, int(jwt.decode(token, get_settings().secret_key, ["HS256"])["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        return None
    return user if user and user.role in ("admin", "social_worker") else None


@router.websocket("/ws/dashboard")
async def dashboard_ws(ws: WebSocket) -> None:
    with Session(engine) as db:
        allowed = _staff_from_cookie(ws, db) is not None
    if not allowed:
        await ws.close(code=4401)
        return
    await ws.accept()
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; the dashboard only listens
    except WebSocketDisconnect:
        hub.disconnect(ws)


@router.websocket("/ws/calls/{call_id}")
async def call_ws(ws: WebSocket, call_id: int) -> None:
    with Session(engine) as db:
        user = _staff_from_cookie(ws, db)
        call = db.get(Call, call_id)
        elder = db.get(Elder, call.elder_id) if call else None
        denied = False
        if user is not None and elder is not None:
            try:
                check_elder_access(user, elder)
            except HTTPException:
                denied = True
    rt = service.runtimes.get(call_id)
    if user is None:
        await ws.close(code=4401)
        return
    if elder is None or rt is None:
        await ws.close(code=4404)
        return
    if denied:
        await ws.close(code=4403)
        return
    await ws.accept()

    async def sender() -> None:
        while True:
            await ws.send_json(await rt.outbox.get())

    send_task = asyncio.create_task(sender())
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            try:
                if kind == "text":
                    conf = msg.get("confidence")
                    await service.handle_turn(
                        call_id,
                        text=str(msg.get("text", ""))[:2000],
                        raw_input=str(msg["jyutping"])[:400] if msg.get("jyutping") else None,
                        confidence=(
                            float(conf)
                            if isinstance(conf, int | float) and 0 <= conf <= 1
                            else None
                        ),
                    )
                elif kind == "audio":
                    await service.handle_turn(
                        call_id, audio=base64.b64decode(msg.get("data", ""), validate=True)
                    )
                elif kind == "end":
                    await service.end_call(call_id, "aborted")
                else:
                    rt.emit({"type": "error", "message": f"unknown message type: {kind}"})
            except (HTTPException, binascii.Error) as exc:
                detail = exc.detail if isinstance(exc, HTTPException) else "invalid base64 audio"
                rt.emit({"type": "error", "message": detail})
            if rt.session.ended:
                await asyncio.sleep(0.05)  # let the sender flush call.ended
                while not rt.outbox.empty():
                    await asyncio.sleep(0.02)
                break
    except WebSocketDisconnect:
        logger.info("call %s websocket disconnected", call_id)
    finally:
        send_task.cancel()
        try:
            await ws.close()
        except RuntimeError:
            pass
