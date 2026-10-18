"""Shared helpers for read-only dashboard routers."""

from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.models import Alert, Call, Elder, RiskAssessment

HKT = timedelta(hours=8)


def hk_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (dt + HKT).date().isoformat()


def today_hk() -> str:
    return hk_date(datetime.now(UTC))


def latest_risk(session: Session, elder_id: int) -> RiskAssessment | None:
    return session.exec(
        select(RiskAssessment)
        .where(RiskAssessment.elder_id == elder_id)
        .order_by(RiskAssessment.date.desc(), RiskAssessment.id.desc())
    ).first()


def open_red_alert(session: Session, elder_id: int) -> Alert | None:
    return session.exec(
        select(Alert).where(
            Alert.elder_id == elder_id, Alert.level == "red", Alert.resolved_at.is_(None)
        )
    ).first()


def effective_risk(session: Session, elder_id: int) -> tuple[str | None, list[dict]]:
    """Latest level and reasons. An unresolved Red alert is never downgraded."""
    ra = latest_risk(session, elder_id)
    level, reasons = (ra.level, list(ra.reasons)) if ra else (None, [])
    alert = open_red_alert(session, elder_id)
    if alert and level != "red":
        level = "red"
        reasons = [
            {
                "code": "OPEN_RED_ALERT",
                "text_en": alert.reason_en,
                "text_zh": alert.reason_zh,
                "evidence_call_ids": [alert.call_id] if alert.call_id else [],
            }
        ] + reasons
    return level, reasons


def elder_brief(e: Elder) -> dict:
    return {
        "id": e.id,
        "display_name": e.display_name,
        "display_name_en": e.display_name_en,
        "age": e.age,
        "district": e.district,
        "language": e.language,
        "status": e.status,
        "is_synthetic": e.is_synthetic,
    }


def last_call(session: Session, elder_id: int) -> Call | None:
    return session.exec(
        select(Call).where(Call.elder_id == elder_id).order_by(Call.started_at.desc())
    ).first()
