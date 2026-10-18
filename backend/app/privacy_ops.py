"""Privacy operations (SPEC 13): data-access export, consent withdrawal, and purging a person's data.

PDPO DPP2 (retention), DPP5/6 (access and correction). Purging removes everything personal; it keeps
the anonymised person row, the consent record (proof of consent and its withdrawal) and the audit log
(which holds ids, never content) so the system can still account for what happened.
"""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlmodel import Session, select

from app.config import get_settings
from app.models import (
    ActionLog,
    Alert,
    AuditLog,
    Call,
    CallSummary,
    Consent,
    Elder,
    FamilyDigest,
    MemoryFact,
    Nudge,
    RiskAssessment,
    Turn,
    User,
    utcnow,
)
from app.realtime.hub import hub
from app.security.audit import audit


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def latest_consent(db: Session, elder_id: int) -> Consent | None:
    return db.exec(
        select(Consent).where(Consent.elder_id == elder_id).order_by(Consent.consented_at.desc())
    ).first()


def consent_status(db: Session, elder_id: int) -> dict:
    c = latest_consent(db, elder_id)
    elder = db.get(Elder, elder_id)
    due = None
    if c and c.withdrawn_at:
        due = _aware(c.withdrawn_at) + timedelta(days=get_settings().withdrawal_grace_days)
    return {
        "has_consent": c is not None,
        "active": bool(c and c.withdrawn_at is None and c.scope_calls),
        "consented_at": c.consented_at if c else None,
        "method": c.method if c else None,
        "witnessed_by": c.witnessed_by if c else None,
        "scope_calls": c.scope_calls if c else None,
        "scope_family_digest": c.scope_family_digest if c else None,
        "retention_days": c.scope_data_retention_days if c else None,
        "withdrawn_at": c.withdrawn_at if c else None,
        "deletion_due_at": due,
        "elder_status": elder.status if elder else None,
    }


def export_elder(db: Session, elder_id: int) -> dict:
    """Everything held about one person, decrypted, for a data-access request (DPP6). Admin only."""
    elder = db.get(Elder, elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    calls = db.exec(select(Call).where(Call.elder_id == elder_id).order_by(Call.started_at)).all()
    ids = [c.id for c in calls] or [0]
    turns = db.exec(
        select(Turn).where(Turn.call_id.in_(ids)).order_by(Turn.call_id, Turn.index)
    ).all()
    summaries = {
        s.call_id: s for s in db.exec(select(CallSummary).where(CallSummary.call_id.in_(ids)))
    }
    alerts = db.exec(select(Alert).where(Alert.elder_id == elder_id)).all()
    entity_ids = {"elder": {elder_id}, "call": set(ids), "alert": {a.id for a in alerts}}
    audits = [
        a
        for a in db.exec(select(AuditLog).order_by(AuditLog.id))
        if a.entity_id in entity_ids.get(a.entity, set())
    ]
    return {
        "exported_at": utcnow().isoformat(),
        "elder": {
            "id": elder.id,
            "display_name": elder.display_name,
            "display_name_en": elder.display_name_en,
            "full_name": elder.full_name,
            "phone": elder.phone,
            "age": elder.age,
            "gender": elder.gender,
            "district": elder.district,
            "language": elder.language,
            "interests": elder.interests,
            "care_notes": elder.care_notes,
            "status": elder.status,
            "is_synthetic": elder.is_synthetic,
        },
        "consent": [
            c.model_dump(mode="json")
            for c in db.exec(select(Consent).where(Consent.elder_id == elder_id))
        ],
        "calls": [
            {
                **c.model_dump(mode="json"),
                "turns": [
                    {
                        "index": t.index,
                        "speaker": t.speaker,
                        "text": t.text,
                        "text_en": t.text_en,
                        "created_at": t.created_at.isoformat(),
                    }
                    for t in turns
                    if t.call_id == c.id
                ],
                "summary": (
                    {
                        "summary_en": summaries[c.id].summary_en,
                        "summary_yue": summaries[c.id].summary_yue,
                        "extraction": summaries[c.id].extraction,
                    }
                    if c.id in summaries
                    else None
                ),
            }
            for c in calls
        ],
        "memory_facts": [
            f.model_dump(mode="json")
            for f in db.exec(select(MemoryFact).where(MemoryFact.elder_id == elder_id))
        ],
        "risk_assessments": [
            r.model_dump(mode="json")
            for r in db.exec(select(RiskAssessment).where(RiskAssessment.elder_id == elder_id))
        ],
        "alerts": [a.model_dump(mode="json") for a in alerts],
        "actions": [
            a.model_dump(mode="json")
            for a in db.exec(select(ActionLog).where(ActionLog.elder_id == elder_id))
        ],
        "nudges": [
            n.model_dump(mode="json")
            for n in db.exec(select(Nudge).where(Nudge.elder_id == elder_id))
        ],
        "family_digests": [
            d.model_dump(mode="json")
            for d in db.exec(select(FamilyDigest).where(FamilyDigest.elder_id == elder_id))
        ],
        "audit_trail": [
            {
                "action": a.action,
                "entity": a.entity,
                "entity_id": a.entity_id,
                "user_id": a.user_id,
                "at": a.created_at.isoformat(),
            }
            for a in audits
        ],
    }


async def withdraw_consent(db: Session, user: User, elder_id: int, notes: str) -> dict:
    """A human records that the person withdrew consent (after the in-person confirmation).

    Calls stop at once, family digests stop, and the data is scheduled for deletion after the grace period.
    """
    from app.conversation import service

    if not notes or not notes.strip():
        raise HTTPException(422, "A note is required (who confirmed it, and how)")
    elder = db.get(Elder, elder_id)
    consent = latest_consent(db, elder_id)
    if elder is None or consent is None:
        raise HTTPException(404, "Not found")
    if consent.withdrawn_at is not None:
        raise HTTPException(409, "Consent is already withdrawn")
    now = utcnow()
    consent.withdrawn_at = now
    elder.status = "withdrawn"
    db.add(consent)
    db.add(elder)
    for alert in db.exec(
        select(Alert).where(
            Alert.elder_id == elder_id, Alert.type == "opt_out_request", Alert.resolved_at.is_(None)
        )
    ):
        alert.acknowledged_by, alert.acknowledged_at = (
            alert.acknowledged_by or user.id,
            alert.acknowledged_at or now,
        )
        alert.resolved_at, alert.outcome, alert.notes = (
            now,
            "no_action_needed",
            f"Consent withdrawn. {notes.strip()}",
        )
        db.add(alert)
    db.add(
        ActionLog(
            elder_id=elder_id,
            user_id=user.id,
            action="no_action_needed",
            notes=f"Consent withdrawn: {notes.strip()}",
        )
    )
    db.commit()
    audit(db, user.id, "consent_withdrawn", "elder", elder_id)
    for call_id, rt in list(service.runtimes.items()):  # hang up any call in progress
        if rt.session.elder_id == elder_id:
            await service.end_call(call_id, "aborted")
    await hub.broadcast({"type": "elder.updated", "elder_id": elder_id, "status": "withdrawn"})
    return consent_status(db, elder_id)


def purge_elder(db: Session, elder_id: int) -> dict:
    """Irreversibly delete a person's personal data (calls, transcripts, summaries, facts, assessments...)."""
    elder = db.get(Elder, elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    call_ids = [c.id for c in db.exec(select(Call).where(Call.elder_id == elder_id))] or [0]
    counts = {}
    for name, model, where in (
        ("turns", Turn, Turn.call_id.in_(call_ids)),
        ("summaries", CallSummary, CallSummary.call_id.in_(call_ids)),
        ("nudges", Nudge, Nudge.elder_id == elder_id),
        ("facts", MemoryFact, MemoryFact.elder_id == elder_id),
        ("assessments", RiskAssessment, RiskAssessment.elder_id == elder_id),
        ("digests", FamilyDigest, FamilyDigest.elder_id == elder_id),
        ("actions", ActionLog, ActionLog.elder_id == elder_id),
        ("alerts", Alert, Alert.elder_id == elder_id),
        ("calls", Call, Call.elder_id == elder_id),
    ):
        rows = db.exec(select(model).where(where)).all()
        counts[name] = len(rows)
        for r in rows:
            db.delete(r)
        db.flush()
    elder.full_name = elder.phone = "[deleted]"
    elder.care_notes = None
    elder.display_name = elder.display_name_en = "(removed)"
    elder.interests = []
    db.add(elder)
    for u in db.exec(select(User).where(User.role == "family")):
        if elder_id in (u.linked_elder_ids or []):
            u.linked_elder_ids = [i for i in u.linked_elder_ids if i != elder_id]
            db.add(u)
    db.commit()
    audit(db, None, "elder_purged", "elder", elder_id)
    return counts
