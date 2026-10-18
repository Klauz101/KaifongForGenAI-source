from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_session
from app.models import Alert, Elder, User
from app.routers.common import elder_brief
from app.routers.elders import _alert
from app.safety import alerts as safety_alerts
from app.safety.alerts import alert_view
from app.security.auth import caseload_ids, require_staff

router = APIRouter(prefix="/api", tags=["alerts"])


@router.get("/alerts")
def list_alerts(
    status: str = "all",
    level: str | None = None,
    user: User = Depends(require_staff),
    session: Session = Depends(get_session),
) -> list[dict]:
    query = select(Alert)
    ids = caseload_ids(user, session)
    if ids is not None:
        query = query.where(Alert.elder_id.in_(ids or [0]))
    if level:
        query = query.where(Alert.level == level)
    out = []
    for a in session.exec(query.order_by(Alert.created_at.desc())):
        item = _alert(a)
        if status != "all" and item["status"] != status:
            continue
        out.append({**item, "elder": elder_brief(session.get(Elder, a.elder_id))})
    return out


class ResolveBody(BaseModel):
    outcome: str
    notes: str


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: int, user: User = Depends(require_staff), session: Session = Depends(get_session)
) -> dict:
    return alert_view(await safety_alerts.acknowledge(session, user, alert_id))


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    body: ResolveBody,
    user: User = Depends(require_staff),
    session: Session = Depends(get_session),
) -> dict:
    """Close an alert. A human must give an outcome and a note; it is written to the action log."""
    return alert_view(
        await safety_alerts.resolve(session, user, alert_id, body.outcome, body.notes)
    )
