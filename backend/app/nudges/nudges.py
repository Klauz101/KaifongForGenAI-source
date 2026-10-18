"""Nudges toward real people (SPEC 9.2): choose one per call, record the answer, track follow-through.

Success is more human contact, not more AI time: a nudge only "worked" when something happened afterwards
(a family call, a centre activity arranged, an outing...).
"""

import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from sqlmodel import Session, select

from app.models import ActionLog, Call, CallSummary, CommunityResource, Elder, Nudge, RiskAssessment

HERE = Path(__file__).parent
RES = HERE.parent / "resources"
KINDS = ["go_outside", "call_family", "meet_friend", "centre_activity"]
FOLLOW_UP_DAYS = 14
# What counts as follow-through: an action a person logged, or something the next calls revealed.
ACTIONS = {"call_family": {"family_contacted"}, "centre_activity": {"centre_activity_arranged"}}
CONTACT = {
    "call_family": {"family_call"},
    "meet_friend": {"friend", "neighbour"},
    "centre_activity": {"centre"},
}


@lru_cache
def lines() -> dict:
    return yaml.safe_load((RES / "nudges.yaml").read_text(encoding="utf-8"))


def load_resource_file() -> list[dict]:
    return json.loads((HERE / "community_resources.json").read_text(encoding="utf-8"))["resources"]


def seed_resources(db: Session) -> int:
    """Insert the sample resources once (all unverified placeholders)."""
    if db.exec(select(CommunityResource)).first():
        return 0
    for r in load_resource_file():
        db.add(CommunityResource(**r))
    db.commit()
    return len(load_resource_file())


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _order(codes: set[str], recent: list[str]) -> list[str]:
    """Candidate kinds, best first, avoiding the last two kinds when there are alternatives."""
    if "HOMEBOUND" in codes:
        base = ["centre_activity", "go_outside", "meet_friend"]
    elif codes & {"LONELINESS", "MOOD_DROP"}:
        base = ["call_family", "meet_friend", "centre_activity"]
    else:
        base = KINDS
    fresh = [k for k in base if k not in recent[:2]]
    return fresh or base


def _match_resource(db: Session, elder: Elder) -> tuple[CommunityResource | None, str | None]:
    """A resource in the person's district, preferring one with an activity matching their interests."""
    rows = db.exec(
        select(CommunityResource).where(
            CommunityResource.district == elder.district, CommunityResource.type != "hotline"
        )
    ).all()
    interests = [i.lower() for i in elder.interests]
    for r in rows:
        for act in r.activities:
            if any(i in act.lower() or act.lower() in i for i in interests):
                return r, act
    return (
        (rows[0], rows[0].activities[0] if rows[0].activities else None) if rows else (None, None)
    )


def _text(kind: str, resource: CommunityResource | None, activity: str | None) -> dict[str, str]:
    ln = lines()
    if kind != "centre_activity":
        return {"yue": ln[kind]["yue"], "en": ln[kind]["en"]}
    if resource is None:
        return dict(ln["centre_activity"]["generic"])
    act_yue = ln["activities_yue"].get(activity or "", activity or "")
    if resource.verified and activity:
        t = ln["centre_activity"]["verified"]
        return {
            "yue": t["yue"].format(centre=resource.name_zh, activity=act_yue),
            "en": t["en"].format(centre=resource.name, activity=activity),
        }
    if activity:  # unverified placeholder: never claim it exists, offer to ask the social worker
        t = ln["centre_activity"]["unverified"]
        return {"yue": t["yue"].format(activity=act_yue), "en": t["en"].format(activity=activity)}
    return dict(ln["centre_activity"]["generic"])


def choose_nudge(db: Session, elder: Elder) -> dict:
    """Pick today's nudge from the person's recent risk reasons, interests and nudge history."""
    ra = db.exec(
        select(RiskAssessment)
        .where(RiskAssessment.elder_id == elder.id)
        .order_by(RiskAssessment.date.desc(), RiskAssessment.id.desc())
    ).first()
    codes = {r["code"] for r in ra.reasons} if ra else set()
    recent_rows = db.exec(
        select(Nudge).where(Nudge.elder_id == elder.id).order_by(Nudge.id.desc()).limit(3)
    ).all()
    kind = _order(codes, [n.type for n in recent_rows])[0]
    resource, activity = _match_resource(db, elder) if kind == "centre_activity" else (None, None)
    return {
        "kind": kind,
        "resource_id": resource.id if resource else None,
        "idea": _text(kind, resource, activity),
    }


def update_followups(db: Session, elder_id: int, now: datetime | None = None) -> int:
    """Mark accepted nudges as followed up when an action was logged or later calls show contact.

    Pending (None) becomes True on evidence, or False once FOLLOW_UP_DAYS pass without any.
    """
    now = now or datetime.now(UTC)
    nudges = db.exec(
        select(Nudge).where(Nudge.elder_id == elder_id, Nudge.elder_response == "interested")
    ).all()
    if not nudges:
        return 0
    calls = {
        c.id: _aware(c.started_at) for c in db.exec(select(Call).where(Call.elder_id == elder_id))
    }
    actions = db.exec(select(ActionLog).where(ActionLog.elder_id == elder_id)).all()
    summaries = db.exec(
        select(CallSummary).where(CallSummary.call_id.in_(list(calls) or [0]))
    ).all()
    changed = 0
    for n in nudges:
        if n.followed_up is True or n.call_id not in calls:
            continue
        start = calls[n.call_id]
        done = any(
            _aware(a.created_at) > start and a.action in ACTIONS.get(n.type, set()) for a in actions
        )
        for s in summaries:
            if calls.get(s.call_id, start) <= start:
                continue
            x = s.extraction
            if CONTACT.get(n.type, set()) & set(x["social_contact"]["value"]) or (
                n.type == "go_outside" and x["went_out_today"]["value"] == "yes"
            ):
                done = True
        new = (
            True
            if done
            else (False if now - start > timedelta(days=FOLLOW_UP_DAYS) else n.followed_up)
        )
        if new != n.followed_up:
            n.followed_up = new
            db.add(n)
            changed += 1
    db.commit()
    return changed


def connection_stats(nudges: list[Nudge]) -> dict:
    """The 'human connection' metric: suggested -> accepted -> followed up."""
    accepted = [n for n in nudges if n.elder_response == "interested"]
    return {
        "suggested": len(nudges),
        "accepted": len(accepted),
        "followed_up": sum(1 for n in accepted if n.followed_up is True),
    }
