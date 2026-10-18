"""Rehearsal helpers for the demo control page (never used with real data).

`fast_forward_decline` lets a presenter show "what happens if nobody visits": time moves on a few days and
the person gets worse, so the real risk rules (not a script) push them to Red.
"""

from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.analysis import risk
from app.models import Call, CallSummary, Elder, RiskAssessment
from app.safety import alerts as safety_alerts
from app.seed.scenarios import DaySpec
from app.seed.seed import _yaml, build_extraction, build_summary

HKT = timedelta(hours=8)


def decline_spec(k: int, days: int) -> DaySpec:
    """The k-th newest simulated day (0 = today): the further back from today, the milder."""
    worst = k == 0
    return DaySpec(
        mood=1 if worst else 2,
        sleep="poor",
        appetite="skipped_meals",
        meds="no" if worst else "partial",
        pain="severe" if k < 2 else "moderate",
        pain_loc="knee",
        went_out="no",
        contact=["none_mentioned"],
        lonely=True,
        confusion=["none"],
        confidence="high",
    )


async def fast_forward_decline(
    db: Session, elder: Elder, days: int, now: datetime | None = None
) -> dict:
    """Shift this person's history back `days` days and append `days` worsening days ending today."""
    now = now or datetime.now(UTC)
    shift = timedelta(days=days)
    for call in db.exec(select(Call).where(Call.elder_id == elder.id)).all():
        call.started_at -= shift
        if call.ended_at:
            call.ended_at -= shift
        db.add(call)
    for row in db.exec(select(RiskAssessment).where(RiskAssessment.elder_id == elder.id)).all():
        db.delete(row)
    db.commit()

    today = (now + HKT).date()
    snippets = _yaml("summary_snippets.yaml")
    hh, mm = map(int, elder.preferred_call_time.split(":"))
    for k in range(days - 1, -1, -1):
        spec = decline_spec(k, days)
        local = datetime.combine(today - timedelta(days=k), datetime.min.time()) + timedelta(
            hours=hh, minutes=mm
        )
        started = (local - HKT).replace(tzinfo=UTC)
        if started > now:
            started = now - timedelta(minutes=10 + k)
        day_start = (
            datetime.combine(today - timedelta(days=k), datetime.min.time()) - HKT
        ).replace(tzinfo=UTC)
        started = max(started, day_start + timedelta(minutes=1))
        call = Call(
            elder_id=elder.id,
            started_at=started,
            ended_at=started + timedelta(seconds=240),
            status="completed",
            mode="seed",
            language=elder.language,
            duration_s=240,
            turn_count=12,
            avg_latency_ms=2000,
            providers_used={"stt": "seed", "tts": "seed", "llm": "seed"},
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        ex = build_extraction(spec, {}, None, "not_offered")
        ex.summary_en, ex.summary_yue = build_summary(spec, snippets)
        db.add(
            CallSummary(
                call_id=call.id,
                extraction=ex.model_dump(),
                summary_en=ex.summary_en,
                summary_yue=ex.summary_yue,
                overall_confidence="high",
                model_used="demo",
            )
        )
    db.commit()

    for d in range(13, -1, -1):  # rebuild the risk history from the engine, as of each day
        risk.assess(db, elder.id, as_of=today - timedelta(days=d), now=now - timedelta(days=d))
    result = risk.assess(db, elder.id, now=now)
    alert_id = None
    if result.level == "red":
        sharp = next((r for r in result.reasons if r["code"] == "SHARP_CHANGE"), None)
        if sharp:
            from app.models import Alert

            already = db.exec(
                select(Alert).where(
                    Alert.elder_id == elder.id, Alert.type == "trend", Alert.resolved_at.is_(None)
                )
            ).first()
            if not already:
                alert = await safety_alerts.raise_alert(
                    db, elder.id, None, "trend", "red", sharp["text_en"], sharp["text_zh"]
                )
                alert_id = alert.id
    await safety_alerts.broadcast_risk(db, elder.id, result)
    return {
        "level": result.level,
        "reasons": [r["code"] for r in result.reasons],
        "alert_id": alert_id,
        "days": days,
    }
