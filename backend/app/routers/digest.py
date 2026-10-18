from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.analysis import digest as digest_service
from app.db import get_session
from app.models import Elder, FamilyDigest, User
from app.security.audit import audit
from app.security.auth import check_elder_access, current_user, require_staff

router = APIRouter(prefix="/api", tags=["digest"])


def view(d: FamilyDigest, for_family: bool = False) -> dict:
    out = {
        "id": d.id,
        "elder_id": d.elder_id,
        "week_start": d.week_start,
        "text_en": d.text_en,
        "text_zh": d.text_zh,
        "generated_at": d.generated_at,
        "sent_simulated_at": d.sent_simulated_at,
    }
    return (
        out
        if not for_family
        else {k: out[k] for k in ("week_start", "text_en", "text_zh", "sent_simulated_at")}
    )


def _elder(db: Session, user: User, elder_id: int) -> Elder:
    elder = db.get(Elder, elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    check_elder_access(user, elder)
    return elder


@router.post("/elders/{elder_id}/digest")
def generate_digest(
    elder_id: int, user: User = Depends(require_staff), db: Session = Depends(get_session)
) -> dict:
    """Generate this week's family digest for staff to preview (not visible to family until marked sent)."""
    elder = _elder(db, user, elder_id)
    if not digest_service.digest_consented(db, elder_id):
        raise HTTPException(409, "This person has not consented to a family digest")
    row = digest_service.generate(db, elder)
    audit(db, user.id, "digest_generate", "elder", elder_id)
    return view(row)


@router.get("/elders/{elder_id}/digest")
def latest_digest(
    elder_id: int, user: User = Depends(require_staff), db: Session = Depends(get_session)
) -> dict | None:
    _elder(db, user, elder_id)
    row = db.exec(
        select(FamilyDigest)
        .where(FamilyDigest.elder_id == elder_id)
        .order_by(FamilyDigest.generated_at.desc())
    ).first()
    return view(row) if row else None


@router.post("/digests/{digest_id}/send")
def send_digest(
    digest_id: int, user: User = Depends(require_staff), db: Session = Depends(get_session)
) -> dict:
    """Sending is simulated in the prototype: it records the time and makes the digest visible to family."""
    row = db.get(FamilyDigest, digest_id)
    if row is None:
        raise HTTPException(404, "Not found")
    _elder(db, user, row.elder_id)
    if not digest_service.digest_consented(db, row.elder_id):
        raise HTTPException(409, "This person has not consented to a family digest")
    row.sent_simulated_at = datetime.now(UTC)
    db.add(row)
    db.commit()
    audit(db, user.id, "digest_send_simulated", "digest", digest_id)
    return view(row)


@router.get("/family/digest")
def family_digest(
    user: User = Depends(current_user), db: Session = Depends(get_session)
) -> list[dict]:
    """What a family member sees: only SENT digests of linked people. Never transcripts or call data."""
    if user.role != "family":
        raise HTTPException(403, "Family accounts only")
    out = []
    for elder_id in user.linked_elder_ids:
        elder = db.get(Elder, elder_id)
        row = db.exec(
            select(FamilyDigest)
            .where(FamilyDigest.elder_id == elder_id, FamilyDigest.sent_simulated_at.is_not(None))
            .order_by(FamilyDigest.generated_at.desc())
        ).first()
        if elder and row and digest_service.digest_consented(db, elder_id):
            audit(db, user.id, "digest_read", "digest", row.id)
            out.append(
                {
                    "elder": {
                        "display_name": elder.display_name,
                        "display_name_en": elder.display_name_en,
                    },
                    **view(row, for_family=True),
                }
            )
    return out
