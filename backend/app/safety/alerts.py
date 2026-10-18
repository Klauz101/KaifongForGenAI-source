"""Alert creation and the human workflow: acknowledge -> resolve with a mandatory outcome and note.

Everything an alert does is audited. Dashboards are told live over the hub.
"""

import logging
from datetime import datetime

from fastapi import HTTPException
from sqlmodel import Session, select

from app.analysis import risk
from app.models import ActionLog, Alert, Elder, User, utcnow
from app.nudges.nudges import update_followups
from app.realtime.hub import hub
from app.security.audit import audit

logger = logging.getLogger(__name__)
OUTCOMES = (
    "home_visit",
    "phone_call",
    "referral",
    "family_contacted",
    "centre_activity_arranged",
    "no_action_needed",
)


def alert_view(a: Alert) -> dict:
    status = "resolved" if a.resolved_at else "acknowledged" if a.acknowledged_at else "open"
    return {
        "id": a.id,
        "elder_id": a.elder_id,
        "call_id": a.call_id,
        "level": a.level,
        "type": a.type,
        "reason_en": a.reason_en,
        "reason_zh": a.reason_zh,
        "created_at": a.created_at,
        "status": status,
        "outcome": a.outcome,
        "notes": a.notes,
        "resolved_at": a.resolved_at,
        "acknowledged_at": a.acknowledged_at,
    }


def elder_names(e: Elder) -> dict:
    return {"id": e.id, "display_name": e.display_name, "display_name_en": e.display_name_en}


async def broadcast_risk(db: Session, elder_id: int, result: risk.RiskResult | None = None) -> None:
    result = result or risk.assess(db, elder_id)
    await hub.broadcast(
        {
            "type": "risk.updated",
            "elder_id": elder_id,
            "level": result.level,
            "reasons": [r["code"] for r in result.reasons],
        }
    )


async def raise_alert(
    db: Session,
    elder_id: int,
    call_id: int | None,
    type_: str,
    level: str,
    reason_en: str,
    reason_zh: str,
) -> Alert:
    """Create an alert, recompute risk, and push both to dashboards immediately."""
    alert = Alert(
        elder_id=elder_id,
        call_id=call_id,
        level=level,
        type=type_,
        reason_en=reason_en,
        reason_zh=reason_zh,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    audit(db, None, f"alert_created:{type_}", "alert", alert.id)
    elder = db.get(Elder, elder_id)
    await hub.broadcast(
        {"type": "alert.created", "alert": alert_view(alert), "elder": elder_names(elder)}
    )
    await broadcast_risk(db, elder_id)
    return alert


async def sync_unreachable_alert(
    db: Session, elder_id: int, result: risk.RiskResult
) -> Alert | None:
    """Missing 2 scheduled calls in a row -> one RED unreachable alert (none if one is already open)."""
    reason = next((r for r in result.reasons if r["code"] == "UNREACHABLE"), None)
    if reason is None:
        return None
    open_one = db.exec(
        select(Alert).where(
            Alert.elder_id == elder_id, Alert.type == "unreachable", Alert.resolved_at.is_(None)
        )
    ).first()
    if open_one:
        return None
    ids = reason["evidence_call_ids"]
    return await raise_alert(
        db,
        elder_id,
        ids[-1] if ids else None,
        "unreachable",
        "red",
        reason["text_en"],
        reason["text_zh"],
    )


def _owned(user: User, alert: Alert, db: Session) -> Elder:
    elder = db.get(Elder, alert.elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    if user.role != "admin" and elder.caseworker_id != user.id:
        raise HTTPException(403, "Not permitted for this person")
    return elder


async def acknowledge(db: Session, user: User, alert_id: int) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(404, "Not found")
    _owned(user, alert, db)
    if alert.resolved_at is None and alert.acknowledged_at is None:
        alert.acknowledged_by, alert.acknowledged_at = user.id, utcnow()
        db.add(alert)
        db.commit()
        audit(db, user.id, "alert_acknowledge", "alert", alert_id)
        await hub.broadcast({"type": "alert.updated", "alert": alert_view(alert)})
    return alert


async def resolve(db: Session, user: User, alert_id: int, outcome: str, notes: str) -> Alert:
    """A human closes the alert. Outcome and a note are mandatory; an action-log entry is written."""
    if outcome not in OUTCOMES:
        raise HTTPException(422, f"outcome must be one of: {', '.join(OUTCOMES)}")
    if not notes or not notes.strip():
        raise HTTPException(422, "A note is required to resolve an alert")
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(404, "Not found")
    _owned(user, alert, db)
    if alert.resolved_at is not None:
        raise HTTPException(409, "Already resolved")
    now: datetime = utcnow()
    if alert.acknowledged_at is None:
        alert.acknowledged_by, alert.acknowledged_at = user.id, now
    alert.resolved_at, alert.outcome, alert.notes = now, outcome, notes.strip()
    db.add(alert)
    db.add(
        ActionLog(
            elder_id=alert.elder_id,
            alert_id=alert.id,
            user_id=user.id,
            action=outcome,
            notes=notes.strip(),
        )
    )
    db.commit()
    audit(db, user.id, f"alert_resolve:{outcome}", "alert", alert_id)
    update_followups(db, alert.elder_id)
    await hub.broadcast({"type": "alert.updated", "alert": alert_view(alert)})
    await broadcast_risk(
        db, alert.elder_id
    )  # releases the "unresolved Red" hold if this was the last
    return alert


def log_action(db: Session, user: User, elder_id: int, action: str, notes: str | None) -> ActionLog:
    if action not in OUTCOMES:
        raise HTTPException(422, f"action must be one of: {', '.join(OUTCOMES)}")
    row = ActionLog(
        elder_id=elder_id, user_id=user.id, action=action, notes=(notes or "").strip() or None
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit(db, user.id, f"action_logged:{action}", "elder", elder_id)
    update_followups(db, elder_id)
    return row
