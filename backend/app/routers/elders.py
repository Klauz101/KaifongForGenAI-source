from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_session
from app.models import (
    ActionLog,
    Alert,
    Call,
    CallSummary,
    CommunityResource,
    Consent,
    Elder,
    MemoryFact,
    Nudge,
    RiskAssessment,
    User,
)
from app.nudges.nudges import connection_stats
from app.routers.common import effective_risk, elder_brief, hk_date, last_call, today_hk
from app.safety import alerts as safety_alerts
from app.security.audit import audit
from app.security.auth import caseload_ids, check_elder_access, current_user, require_staff

router = APIRouter(prefix="/api", tags=["elders"])


@router.get("/elders")
def list_elders(
    user: User = Depends(current_user), session: Session = Depends(get_session)
) -> list[dict]:
    if user.role == "family":  # minimal view of linked elders only
        rows = session.exec(select(Elder).where(Elder.id.in_(user.linked_elder_ids or [0]))).all()
        return [
            {"id": e.id, "display_name": e.display_name, "display_name_en": e.display_name_en}
            for e in rows
        ]
    query = select(Elder)
    ids = caseload_ids(user, session)
    if ids is not None:
        query = query.where(Elder.id.in_(ids or [0]))
    out = []
    for e in session.exec(query.order_by(Elder.id)):
        level, _ = effective_risk(session, e.id)
        out.append({**elder_brief(e), "level": level})
    return out


def _open_alert(session: Session, elder_id: int) -> dict | None:
    """The most urgent unresolved alert for a person (red first), for the triage card."""
    rows = session.exec(
        select(Alert)
        .where(Alert.elder_id == elder_id, Alert.resolved_at.is_(None))
        .order_by(Alert.created_at.desc())
    ).all()
    rows.sort(key=lambda a: 0 if a.level == "red" else 1)
    a = rows[0] if rows else None
    if a is None:
        return None
    return {
        "id": a.id,
        "level": a.level,
        "type": a.type,
        "status": "acknowledged" if a.acknowledged_at else "open",
    }


@router.get("/today")
def today(user: User = Depends(require_staff), session: Session = Depends(get_session)) -> dict:
    ids = caseload_ids(user, session)
    query = select(Elder).where(Elder.status != "withdrawn")
    if ids is not None:
        query = query.where(Elder.id.in_(ids or [0]))
    day, cards, completed = today_hk(), [], 0
    for e in session.exec(query):
        level, reasons = effective_risk(session, e.id)
        call = last_call(session, e.id)
        called_today = call is not None and hk_date(call.started_at) == day
        if called_today and call.status in ("completed", "safety_escalated"):
            completed += 1
        bucket = level if level in ("red", "amber") else ("green" if called_today else "not_called")
        open_alert = _open_alert(session, e.id)
        cards.append(
            {
                **elder_brief(e),
                "open_alert": open_alert,
                "level": level,
                "bucket": bucket,
                "reason_count": len(reasons),
                "top_reasons": reasons[:2],
                "last_call_at": call.started_at if call else None,
                "last_call_status": call.status if call else None,
            }
        )
    order = {"red": 0, "amber": 1, "green": 2, "not_called": 3}
    cards.sort(key=lambda c: (order[c["bucket"]], -c["reason_count"], c["display_name_en"]))
    return {
        "date": day,
        "summary": {
            "need_attention": sum(c["bucket"] in ("red", "amber") for c in cards),
            "urgent": sum(c["bucket"] == "red" for c in cards),
            "calls_completed": completed,
            "total": len(cards),
        },
        "cards": cards,
    }


def _trend_point(call: Call, summary: CallSummary | None) -> dict:
    point = {"date": hk_date(call.started_at), "call_id": call.id, "status": call.status}
    if summary:
        x = summary.extraction
        point.update(
            mood=x["mood"]["value"],
            sleep=x["sleep"]["value"],
            appetite=x["appetite"]["value"],
            meds=x["meds_taken"]["value"],
            pain=x["pain"]["value"],
            went_out=x["went_out_today"]["value"],
            lonely=x["loneliness_expressed"]["value"],
            confidence=summary.overall_confidence,
        )
    return point


@router.get("/elders/{elder_id}")
def elder_detail(
    elder_id: int, user: User = Depends(require_staff), session: Session = Depends(get_session)
) -> dict:
    elder = session.get(Elder, elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    check_elder_access(user, elder)
    level, reasons = effective_risk(session, elder_id)
    calls = session.exec(
        select(Call).where(Call.elder_id == elder_id).order_by(Call.started_at)
    ).all()
    summaries = {
        s.call_id: s
        for s in session.exec(
            select(CallSummary).where(CallSummary.call_id.in_([c.id for c in calls] or [0]))
        )
    }
    consent = session.exec(
        select(Consent).where(Consent.elder_id == elder_id).order_by(Consent.consented_at.desc())
    ).first()
    nudges = session.exec(select(Nudge).where(Nudge.elder_id == elder_id)).all()
    resources = {r.id: r for r in session.exec(select(CommunityResource))}
    call_dates = {c.id: c.started_at for c in calls}
    return {
        "elder": {
            **elder_brief(elder),
            "full_name": elder.full_name,
            "phone": elder.phone,
            "gender": elder.gender,
            "interests": elder.interests,
            "care_notes": elder.care_notes,
            "preferred_call_time": elder.preferred_call_time,
        },
        "consent": consent
        and {
            "method": consent.method,
            "witnessed_by": consent.witnessed_by,
            "consented_at": consent.consented_at,
            "withdrawn_at": consent.withdrawn_at,
            "scope_family_digest": consent.scope_family_digest,
        },
        "risk": {"level": level, "reasons": reasons},
        "risk_history": [
            {"date": r.date, "level": r.level, "reason_count": len(r.reasons)}
            for r in session.exec(
                select(RiskAssessment)
                .where(RiskAssessment.elder_id == elder_id)
                .order_by(RiskAssessment.date)
            )
        ],
        "trends": [_trend_point(c, summaries.get(c.id)) for c in calls],
        "memory_facts": [
            {
                "id": f.id,
                "fact": f.fact,
                "fact_yue": f.fact_yue,
                "category": f.category,
                "active": f.active,
            }
            for f in session.exec(select(MemoryFact).where(MemoryFact.elder_id == elder_id))
        ],
        "alerts": [
            _alert(a)
            for a in session.exec(
                select(Alert).where(Alert.elder_id == elder_id).order_by(Alert.created_at.desc())
            )
        ],
        "actions": [
            {
                "id": a.id,
                "action": a.action,
                "notes": a.notes,
                "created_at": a.created_at,
                "alert_id": a.alert_id,
            }
            for a in session.exec(
                select(ActionLog)
                .where(ActionLog.elder_id == elder_id)
                .order_by(ActionLog.created_at.desc())
            )
        ],
        "nudges": [
            {
                "id": n.id,
                "type": n.type,
                "text_en": n.text_en,
                "text_yue": n.text_yue,
                "elder_response": n.elder_response,
                "followed_up": n.followed_up,
                "date": hk_date(call_dates[n.call_id]) if n.call_id in call_dates else None,
                "resource": resources[n.resource_id].name if n.resource_id in resources else None,
                "resource_verified": (
                    resources[n.resource_id].verified if n.resource_id in resources else None
                ),
            }
            for n in nudges
        ],
        "connection": connection_stats(nudges),
        "calls": [
            {
                "id": c.id,
                "started_at": c.started_at,
                "status": c.status,
                "duration_s": c.duration_s,
                "mode": c.mode,
                "summary_en": summaries[c.id].summary_en if c.id in summaries else None,
                "summary_yue": summaries[c.id].summary_yue if c.id in summaries else None,
            }
            for c in reversed(calls)
        ],
    }


def _alert(a: Alert) -> dict:
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


class MemoryToggle(BaseModel):
    active: bool


@router.patch("/elders/{elder_id}/memory/{fact_id}")
def toggle_memory(
    elder_id: int,
    fact_id: int,
    body: MemoryToggle,
    user: User = Depends(require_staff),
    session: Session = Depends(get_session),
) -> dict:
    elder = session.get(Elder, elder_id)
    fact = session.get(MemoryFact, fact_id)
    if elder is None or fact is None or fact.elder_id != elder_id:
        raise HTTPException(404, "Not found")
    check_elder_access(user, elder)
    fact.active = body.active
    session.add(fact)
    session.commit()
    audit(
        session,
        user.id,
        "memory_activate" if body.active else "memory_deactivate",
        "memory_fact",
        fact_id,
    )
    return {"id": fact.id, "active": fact.active}


class ActionBody(BaseModel):
    action: str
    notes: str | None = None


class StatusBody(BaseModel):
    status: str


@router.post("/elders/{elder_id}/actions")
def post_action(
    elder_id: int,
    body: ActionBody,
    user: User = Depends(require_staff),
    session: Session = Depends(get_session),
) -> dict:
    """Log a visit, call, referral... (e.g. from the triage card). Audited."""
    elder = session.get(Elder, elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    check_elder_access(user, elder)
    row = safety_alerts.log_action(session, user, elder_id, body.action, body.notes)
    return {"id": row.id, "action": row.action, "notes": row.notes, "created_at": row.created_at}


@router.patch("/elders/{elder_id}/status")
def set_status(
    elder_id: int,
    body: StatusBody,
    user: User = Depends(require_staff),
    session: Session = Depends(get_session),
) -> dict:
    """Pause or resume daily calls (e.g. the person changed their mind after an opt-out request).
    Withdrawing consent is a separate, fuller flow (Phase 6)."""
    if body.status not in ("active", "paused"):
        raise HTTPException(422, "status must be 'active' or 'paused'")
    elder = session.get(Elder, elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    check_elder_access(user, elder)
    if elder.status == "withdrawn":
        raise HTTPException(409, "Consent was withdrawn; calls cannot be resumed here")
    elder.status = body.status
    session.add(elder)
    session.commit()
    audit(session, user.id, f"elder_status:{body.status}", "elder", elder_id)
    return {"id": elder.id, "status": elder.status}
