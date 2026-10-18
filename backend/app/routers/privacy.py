"""Privacy rights and oversight (SPEC 13): data access, correction, consent withdrawal, retention, audit."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, func, select

from app import privacy_ops
from app.analysis import risk
from app.db import get_session
from app.jobs.retention import run_retention
from app.models import AuditLog, Call, CallSummary, Elder, User
from app.realtime.hub import hub
from app.safety.alerts import broadcast_risk
from app.schemas import Extraction
from app.security.audit import audit
from app.security.auth import check_elder_access, require_staff

router = APIRouter(prefix="/api", tags=["privacy"])
EDITABLE = (
    "mood",
    "sleep",
    "appetite",
    "meds_taken",
    "pain",
    "went_out_today",
    "loneliness_expressed",
)


def admin_only(user: User = Depends(require_staff)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admin only")
    return user


def _elder(db: Session, user: User, elder_id: int) -> Elder:
    elder = db.get(Elder, elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    check_elder_access(user, elder)
    return elder


@router.get("/elders/{elder_id}/export")
def export(
    elder_id: int, user: User = Depends(admin_only), db: Session = Depends(get_session)
) -> dict:
    """Data-access export (PDPO DPP6): everything held about one person. Audited."""
    data = privacy_ops.export_elder(db, elder_id)
    audit(db, user.id, "export_data", "elder", elder_id)
    return data


@router.get("/elders/{elder_id}/consent")
def consent(
    elder_id: int, user: User = Depends(require_staff), db: Session = Depends(get_session)
) -> dict:
    _elder(db, user, elder_id)
    return privacy_ops.consent_status(db, elder_id)


class WithdrawBody(BaseModel):
    notes: str


@router.post("/elders/{elder_id}/consent/withdraw")
async def withdraw(
    elder_id: int,
    body: WithdrawBody,
    user: User = Depends(require_staff),
    db: Session = Depends(get_session),
) -> dict:
    """Record that the person withdrew consent (confirmed in person). Calls stop now; data is purged later."""
    _elder(db, user, elder_id)
    return await privacy_ops.withdraw_consent(db, user, elder_id, body.notes)


@router.post("/elders/{elder_id}/purge")
def purge(
    elder_id: int, user: User = Depends(admin_only), db: Session = Depends(get_session)
) -> dict:
    """Delete a withdrawn person's data now instead of waiting for the grace period."""
    elder = db.get(Elder, elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    if elder.status != "withdrawn":
        raise HTTPException(409, "Only people who have withdrawn consent can be purged")
    return privacy_ops.purge_elder(db, elder_id)


class RetentionBody(BaseModel):
    dry_run: bool = True


@router.post("/admin/retention/run")
def retention(
    body: RetentionBody, user: User = Depends(admin_only), db: Session = Depends(get_session)
) -> dict:
    return {"dry_run": body.dry_run, **run_retention(db, dry_run=body.dry_run)}


@router.get("/audit")
def audit_log(
    action: str | None = None,
    entity: str | None = None,
    user_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    _: User = Depends(admin_only),
    db: Session = Depends(get_session),
) -> dict:
    """Admin audit viewer. The log holds who did what to which record, never the content itself."""
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action.startswith(action))
    if entity:
        query = query.where(AuditLog.entity == entity)
    if user_id is not None:
        query = query.where(AuditLog.user_id == user_id)
    total = db.exec(select(func.count()).select_from(query.subquery())).one()
    rows = db.exec(
        query.order_by(AuditLog.id.desc()).offset(max(0, offset)).limit(min(max(limit, 1), 500))
    ).all()
    names = {u.id: u.name for u in db.exec(select(User))}
    return {
        "total": total,
        "items": [
            {
                "id": a.id,
                "at": a.created_at,
                "user_id": a.user_id,
                "user": names.get(a.user_id, "system"),
                "action": a.action,
                "entity": a.entity,
                "entity_id": a.entity_id,
            }
            for a in rows
        ],
    }


class Correction(BaseModel):
    field: str
    value: str | int | bool | None


@router.patch("/calls/{call_id}/extraction")
async def correct_extraction(
    call_id: int,
    body: Correction,
    user: User = Depends(require_staff),
    db: Session = Depends(get_session),
) -> dict:
    """Social workers can correct an extracted field (DPP2). The edit is audited and risk is recomputed."""
    call = db.get(Call, call_id)
    elder = db.get(Elder, call.elder_id) if call else None
    summary = (
        db.exec(select(CallSummary).where(CallSummary.call_id == call_id)).first() if call else None
    )
    if call is None or elder is None or summary is None:
        raise HTTPException(404, "Not found")
    check_elder_access(user, elder)
    if body.field not in EDITABLE:
        raise HTTPException(422, f"field must be one of: {', '.join(EDITABLE)}")
    data = dict(summary.extraction)
    old = data[body.field]["value"]
    data[body.field] = {**data[body.field], "value": body.value}
    try:
        Extraction.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(422, "That value is not allowed for this field") from exc
    data["corrections"] = [
        *data.get("corrections", []),
        {
            "field": body.field,
            "old": old,
            "new": body.value,
            "by": user.id,
            "at": datetime.now(UTC).isoformat(),
        },
    ]
    summary.extraction = data
    db.add(summary)
    db.commit()
    audit(db, user.id, f"extraction_edit:{body.field}", "call", call_id)
    result = risk.assess(db, call.elder_id)
    await broadcast_risk(db, call.elder_id, result)
    await hub.broadcast(
        {"type": "call.summary_ready", "call_id": call_id, "elder_id": call.elder_id}
    )
    return {"call_id": call_id, "field": body.field, "value": body.value, "level": result.level}
