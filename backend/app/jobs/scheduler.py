"""Daily call schedule (simulated) and the unreachable rule (SPEC 8.3b).

Real telephony is Phase 4b; until then a missed attempt is recorded by this function (the demo
control page and tests call it). Two misses in a row raise a RED `unreachable` alert.
"""

import logging

from sqlmodel import Session

from app.analysis import risk
from app.models import Call, Elder, utcnow
from app.safety.alerts import broadcast_risk, sync_unreachable_alert

logger = logging.getLogger(__name__)


async def finalize_no_answer(db: Session, elder_id: int) -> dict:
    """After a no-answer call row exists: recompute risk and raise the unreachable alert if due."""
    result = risk.assess(db, elder_id)
    alert = await sync_unreachable_alert(db, elder_id, result)
    if alert is None:
        await broadcast_risk(db, elder_id, result)
    return {"level": result.level, "alert_id": alert.id if alert else None}


async def record_no_answer(db: Session, elder_id: int) -> dict:
    """Record one unanswered scheduled call, recompute risk, and alert if this is the second in a row."""
    elder = db.get(Elder, elder_id)
    if elder is None:
        raise ValueError("unknown elder")
    now = utcnow()
    call = Call(
        elder_id=elder_id,
        started_at=now,
        ended_at=now,
        status="no_answer",
        mode="phone",
        language=elder.language,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return {"call_id": call.id, **await finalize_no_answer(db, elder_id)}
