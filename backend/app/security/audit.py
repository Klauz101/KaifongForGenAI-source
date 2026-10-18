"""Audit log writer."""

from sqlmodel import Session

from app.models import AuditLog


def audit(
    session: Session, user_id: int | None, action: str, entity: str, entity_id: int | None
) -> None:
    session.add(AuditLog(user_id=user_id, action=action, entity=entity, entity_id=entity_id))
    session.commit()
