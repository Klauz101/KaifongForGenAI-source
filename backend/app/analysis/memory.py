"""Memory across calls (SPEC 7.5): save facts, dedupe, retrieve for the next call, expire."""

from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

from sqlmodel import Session, select

from app.models import MemoryFact, utcnow

SIMILARITY = 0.8
PRIORITY = {"event": 0, "health": 1}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def expire_facts(db: Session, elder_id: int, now: datetime | None = None) -> int:
    """Deactivate facts whose expiry has passed (e.g. an event that already happened)."""
    now = now or utcnow()
    n = 0
    for f in db.exec(
        select(MemoryFact).where(MemoryFact.elder_id == elder_id, MemoryFact.active.is_(True))
    ):
        if f.expires_at and _aware(f.expires_at) <= now:
            f.active = False
            db.add(f)
            n += 1
    db.commit()
    return n


def save_facts(
    db: Session, elder_id: int, call_id: int, facts: list[dict], now: datetime | None = None
) -> list[MemoryFact]:
    """Store new facts; skip near-duplicates of active ones. Returns the facts actually saved."""
    now = now or utcnow()
    active = db.exec(
        select(MemoryFact).where(MemoryFact.elder_id == elder_id, MemoryFact.active.is_(True))
    ).all()
    known = [f.fact.lower() for f in active]
    saved = []
    for item in facts:
        text = item["fact_en"].strip()
        if not text or any(
            SequenceMatcher(None, text.lower(), k).ratio() >= SIMILARITY for k in known
        ):
            continue
        days = item.get("expires_in_days")
        fact = MemoryFact(
            elder_id=elder_id,
            fact=text,
            fact_yue=item["fact_yue"].strip(),
            category=item["category"],
            source_call_id=call_id,
            created_at=now,
            expires_at=now + timedelta(days=days) if days else None,
        )
        db.add(fact)
        known.append(text.lower())
        saved.append(fact)
    db.commit()
    return saved


def retrieve_facts(
    db: Session, elder_id: int, limit: int = 5, now: datetime | None = None
) -> list[dict]:
    """Up to `limit` most recent active facts; event/health facts from the last 7 days come first."""
    now = now or utcnow()
    expire_facts(db, elder_id, now)
    rows = db.exec(
        select(MemoryFact)
        .where(MemoryFact.elder_id == elder_id, MemoryFact.active.is_(True))
        .order_by(MemoryFact.created_at.desc())
    ).all()
    week_ago = now - timedelta(days=7)
    rows.sort(key=lambda f: PRIORITY.get(f.category, 2) if _aware(f.created_at) >= week_ago else 3)
    return [{"yue": f.fact_yue, "en": f.fact, "category": f.category} for f in rows[:limit]]
