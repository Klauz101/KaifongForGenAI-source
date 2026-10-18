import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.config import get_settings
from app.conversation import service
from app.db import get_session
from app.jobs.scheduler import record_no_answer
from app.models import Alert, Elder, User
from app.providers.registry import get_providers
from app.realtime.hub import hub
from app.routers.common import effective_risk
from app.security.audit import audit
from app.security.auth import require_staff
from app.seed.demo_tools import fast_forward_decline
from app.seed.seed import reset_and_seed

router = APIRouter(prefix="/api/demo", tags=["demo"])
RED_FLAG_LINES = {
    "yue": "我尋日跌親，起唔到身",
    "en": "I fell yesterday and I can't get up properly",
}


def demo_admin(user: User = Depends(require_staff)) -> User:
    """Rehearsal tools change data on purpose: admin only, and only in dev/demo environments."""
    if user.role != "admin":
        raise HTTPException(403, "Admin only")
    if get_settings().app_env not in ("dev", "demo"):
        raise HTTPException(403, "Demo tools are disabled outside dev/demo environments")
    return user


def _elder(db: Session, elder_id: int) -> Elder:
    elder = db.get(Elder, elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    return elder


@router.get("/status")
def status(user: User = Depends(demo_admin), db: Session = Depends(get_session)) -> dict:
    """Everything the demo control page needs in one call."""
    live = {rt.session.elder_id: call_id for call_id, rt in service.runtimes.items()}
    people = []
    for e in db.exec(select(Elder).order_by(Elder.id)):
        level, _ = effective_risk(db, e.id)
        people.append(
            {
                "id": e.id,
                "display_name": e.display_name,
                "display_name_en": e.display_name_en,
                "language": e.language,
                "status": e.status,
                "level": level,
                "call_in_progress": e.id in live,
            }
        )
    return {
        "app_env": get_settings().app_env,
        "providers": get_providers().names(),
        "people": people,
        "calls_in_progress": len(live),
        "open_alerts": len(db.exec(select(Alert).where(Alert.resolved_at.is_(None))).all()),
    }


@router.post("/reset")
async def reset(user: User = Depends(demo_admin)) -> dict:
    """Wipe and reseed the synthetic data (live calls are dropped). Accounts keep the same ids, so you stay signed in."""
    await service.wait_background()
    service.runtimes.clear()
    logins = await asyncio.to_thread(reset_and_seed)
    await hub.broadcast({"type": "demo.reset"})
    return {"ok": True, "logins": logins}


class DeclineBody(BaseModel):
    days: int = Field(default=3, ge=1, le=7)


@router.post("/decline/{elder_id}")
async def decline(
    elder_id: int,
    body: DeclineBody | None = None,
    user: User = Depends(demo_admin),
    db: Session = Depends(get_session),
) -> dict:
    """Fast-forward: a few more days pass and the person gets worse; the real risk rules do the rest."""
    elder = _elder(db, elder_id)
    audit(db, user.id, "demo_decline", "elder", elder_id)
    return await fast_forward_decline(db, elder, (body or DeclineBody()).days)


@router.post("/inject/{elder_id}")
async def inject_red_flag(
    elder_id: int, user: User = Depends(demo_admin), db: Session = Depends(get_session)
) -> dict:
    """Say the emergency line on behalf of the older person in their live call (for rehearsing the safety path)."""
    _elder(db, elder_id)
    call_id = next(
        (cid for cid, rt in service.runtimes.items() if rt.session.elder_id == elder_id), None
    )
    if call_id is None:
        raise HTTPException(
            409, "No call in progress for this person: start the call in the simulator first"
        )
    language = service.runtimes[call_id].session.language
    audit(db, user.id, "demo_inject_red_flag", "call", call_id)
    await service.handle_turn(call_id, text=RED_FLAG_LINES.get(language, RED_FLAG_LINES["en"]))
    return {"call_id": call_id, "injected": RED_FLAG_LINES.get(language, RED_FLAG_LINES["en"])}


@router.post("/missed-call/{elder_id}")
async def missed_call(
    elder_id: int, user: User = Depends(demo_admin), db: Session = Depends(get_session)
) -> dict:
    """Record one unanswered scheduled call (two in a row raise RED unreachable)."""
    _elder(db, elder_id)
    audit(db, user.id, "demo_missed_call", "elder", elder_id)
    return await record_no_answer(db, elder_id)
