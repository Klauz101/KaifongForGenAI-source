"""Retention job (SPEC 13, DPP2): delete what we no longer need, and purge withdrawn people after the grace period.

Per person the retention period is the shorter of DATA_RETENTION_DAYS and what they consented to.
Raw audio is never kept by default; if RETAIN_AUDIO was on for evaluation, old files go here too.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.config import get_settings
from app.db import engine
from app.models import Call, CallSummary, Elder, MemoryFact, RiskAssessment, Turn
from app.privacy_ops import _aware, latest_consent, purge_elder
from app.security.audit import audit
from app.security.crypto import DATA_DIR

logger = logging.getLogger(__name__)
AUDIO_DIR = DATA_DIR / "audio"


def delete_old_audio(now: float | None = None, dry_run: bool = False) -> int:
    now = now or time.time()
    limit = get_settings().audio_retention_hours * 3600
    n = 0
    if AUDIO_DIR.exists():
        for f in AUDIO_DIR.iterdir():
            if f.is_file() and now - f.stat().st_mtime > limit:
                n += 1
                if not dry_run:
                    f.unlink()
    return n


def run_retention(db: Session, now: datetime | None = None, dry_run: bool = False) -> dict:
    now = now or datetime.now(UTC)
    s = get_settings()
    counts = {"turns": 0, "summaries": 0, "facts": 0, "assessments": 0, "purged": 0, "audio": 0}
    for elder in db.exec(select(Elder)).all():
        consent = latest_consent(db, elder.id)
        if (
            elder.status == "withdrawn"
            and consent
            and consent.withdrawn_at
            and _aware(consent.withdrawn_at) + timedelta(days=s.withdrawal_grace_days) <= now
        ):
            if (
                db.exec(select(Call).where(Call.elder_id == elder.id)).first() is not None
                or elder.full_name != "[deleted]"
            ):
                counts["purged"] += 1
                if not dry_run:
                    purge_elder(db, elder.id)
            continue
        days = min(
            s.data_retention_days,
            consent.scope_data_retention_days if consent else s.data_retention_days,
        )
        cutoff = now - timedelta(days=days)
        old_calls = [
            c.id
            for c in db.exec(select(Call).where(Call.elder_id == elder.id))
            if _aware(c.started_at) < cutoff
        ]
        batches = (
            ("turns", [t for t in db.exec(select(Turn).where(Turn.call_id.in_(old_calls or [0])))]),
            (
                "summaries",
                [
                    x
                    for x in db.exec(
                        select(CallSummary).where(CallSummary.call_id.in_(old_calls or [0]))
                    )
                ],
            ),
            (
                "facts",
                [
                    f
                    for f in db.exec(select(MemoryFact).where(MemoryFact.elder_id == elder.id))
                    if _aware(f.created_at) < cutoff
                ],
            ),
            (
                "assessments",
                [
                    r
                    for r in db.exec(
                        select(RiskAssessment).where(RiskAssessment.elder_id == elder.id)
                    )
                    if r.date < cutoff.date().isoformat()
                ],
            ),
        )
        for name, rows in batches:
            counts[name] += len(rows)
            if not dry_run:
                for r in rows:
                    db.delete(r)
        db.commit()
    counts["audio"] = delete_old_audio(dry_run=dry_run)
    if not dry_run:
        audit(
            db,
            None,
            "retention_run:" + ",".join(f"{k}={v}" for k, v in counts.items()),
            "system",
            None,
        )
    return counts


async def retention_loop() -> None:
    """Runs once per RETENTION_JOB_INTERVAL_HOURS while the app is up (first run after one interval)."""
    interval = get_settings().retention_job_interval_hours * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            with Session(engine) as db:
                logger.info("retention job: %s", run_retention(db))
        except Exception:
            logger.exception("retention job failed")
