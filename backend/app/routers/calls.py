import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.analysis.pipeline import run_post_call
from app.conversation import service
from app.db import get_session
from app.models import Call, CallSummary, Elder, Turn, User
from app.routers.common import elder_brief
from app.security.audit import audit
from app.security.auth import check_elder_access, require_staff

router = APIRouter(prefix="/api", tags=["calls"])


class StartCall(BaseModel):
    elder_id: int
    mode: str = "simulator"
    language: str | None = None


class TurnText(BaseModel):
    text: str = Field(max_length=2000)
    jyutping: str | None = Field(
        default=None, max_length=400
    )  # original input if the client converted it
    confidence: float | None = Field(
        default=None, ge=0, le=1
    )  # e.g. a browser speech recogniser's score


@router.post("/calls")
async def start_call(body: StartCall, user: User = Depends(require_staff)) -> dict:
    if body.mode != "simulator":
        raise HTTPException(422, "Use /api/telephony/call to place a phone call")
    return await service.start_call(user, body.elder_id, body.language, body.mode)


def _staff_runtime(call_id: int, user: User, db: Session) -> None:
    call = db.get(Call, call_id)
    elder = db.get(Elder, call.elder_id) if call else None
    if call is None or elder is None:
        raise HTTPException(404, "Not found")
    check_elder_access(user, elder)


@router.post("/calls/{call_id}/turn")
async def post_turn(
    call_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_session),
) -> dict:
    """REST fallback for the voice loop: JSON {text} or multipart `audio`. Returns the turn's events."""
    _staff_runtime(call_id, user, db)
    rt = service.runtimes.get(call_id)  # grab now: the runtime is removed when the call ends
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        form = await request.form()
        upload = form.get("audio")
        if upload is None or isinstance(upload, str):
            raise HTTPException(422, "multipart field 'audio' is required")
        await service.handle_turn(call_id, audio=await upload.read())
    else:
        try:
            body = TurnText.model_validate(json.loads(await request.body() or b"{}"))
        except (ValueError, TypeError):
            raise HTTPException(422, 'JSON body {"text": ...} is required') from None
        await service.handle_turn(
            call_id, text=body.text, raw_input=body.jyutping, confidence=body.confidence
        )
    return {"events": rt.drain() if rt else []}


@router.post("/calls/{call_id}/end")
async def end_call(
    call_id: int, user: User = Depends(require_staff), db: Session = Depends(get_session)
) -> dict:
    _staff_runtime(call_id, user, db)
    audit(db, user.id, "end_call", "call", call_id)
    return await service.end_call(call_id, "aborted")


@router.post("/calls/{call_id}/reanalyse")
async def reanalyse(
    call_id: int, user: User = Depends(require_staff), db: Session = Depends(get_session)
) -> dict:
    """Re-run post-call analysis (summary, memory, risk) for a finished call, e.g. after a provider hiccup."""
    _staff_runtime(call_id, user, db)
    if call_id in service.runtimes:
        raise HTTPException(409, "Call is still in progress")
    audit(db, user.id, "reanalyse_call", "call", call_id)
    outcome = await run_post_call(call_id)
    if outcome is None:
        raise HTTPException(422, "Nothing to analyse: the call has no transcript")
    return outcome


@router.get("/audio/filler/{language}")
async def filler(language: str, user: User = Depends(require_staff)) -> dict:
    if language not in ("yue", "en"):
        raise HTTPException(404, "Unknown language")
    return await service.filler(language)


@router.get("/calls/{call_id}")
def call_detail(
    call_id: int, user: User = Depends(require_staff), session: Session = Depends(get_session)
) -> dict:
    call = session.get(Call, call_id)
    elder = session.get(Elder, call.elder_id) if call else None
    if call is None or elder is None:
        raise HTTPException(404, "Not found")
    check_elder_access(user, elder)
    audit(session, user.id, "read_transcript", "call", call_id)
    turns = session.exec(select(Turn).where(Turn.call_id == call_id).order_by(Turn.index)).all()
    summary = session.exec(select(CallSummary).where(CallSummary.call_id == call_id)).first()
    return {
        "call": {
            "id": call.id,
            "started_at": call.started_at,
            "ended_at": call.ended_at,
            "status": call.status,
            "mode": call.mode,
            "language": call.language,
            "duration_s": call.duration_s,
            "turn_count": call.turn_count,
            "safety_triggered": call.safety_triggered,
            "avg_latency_ms": call.avg_latency_ms,
        },
        "elder": elder_brief(elder),
        "summary": summary
        and {
            "extraction": summary.extraction,
            "summary_en": summary.summary_en,
            "summary_yue": summary.summary_yue,
            "overall_confidence": summary.overall_confidence,
            "model_used": summary.model_used,
        },
        "turns": [
            {
                "index": t.index,
                "speaker": t.speaker,
                "text": t.text,
                "text_en": t.text_en,
                "stt_confidence": t.stt_confidence,
                "is_scripted_safety": t.is_scripted_safety,
                "latency_breakdown": t.latency_breakdown,
            }
            for t in turns
        ],
        "has_transcript": bool(turns),
    }
