"""Post-call pipeline (SPEC Phase 3): extraction -> summary -> memory -> nudge outcome -> risk -> events."""

import logging

from sqlmodel import Session, select

from app.analysis import memory, risk
from app.analysis.extraction import extract_call
from app.db import engine as db_engine
from app.models import Call, CallSummary, Elder, Nudge, Turn
from app.nudges.nudges import update_followups
from app.providers.registry import get_providers
from app.realtime.hub import hub
from app.safety.alerts import sync_unreachable_alert
from app.security.pseudonymize import Pseudonymizer
from app.usage import metered

logger = logging.getLogger(__name__)


def _restore_name(text: str, elder: Elder, language: str) -> str:
    return text.replace(
        "[ELDER]", elder.display_name if language == "yue" else elder.display_name_en
    )


async def run_post_call(call_id: int) -> dict | None:
    """Analyse one finished call. Safe to re-run: the summary is replaced, facts are deduplicated."""
    providers = get_providers()
    with Session(db_engine) as db:
        call = db.get(Call, call_id)
        elder = db.get(Elder, call.elder_id) if call else None
        if call is None or elder is None:
            return None
        rows = db.exec(select(Turn).where(Turn.call_id == call_id).order_by(Turn.index)).all()
        turns = [{"index": t.index, "speaker": t.speaker, "text": t.text} for t in rows]
        nudge = db.exec(select(Nudge).where(Nudge.call_id == call_id)).first()
        nudge_turn = nudge_turn_index(nudge, rows)
        pseud = Pseudonymizer(
            elder.full_name, elder.phone, (elder.display_name, elder.display_name_en)
        )
        language, elder_id = call.language, elder.id
    if not any(t["speaker"] == "elder" and t["text"].strip() for t in turns):
        return None  # nothing was said: nothing to analyse

    with metered(call_id, "extract"):
        ex, notes = await extract_call(providers.extractor, turns, pseud, language, nudge_turn)

    with Session(db_engine) as db:
        elder = db.get(Elder, elder_id)
        ex.summary_en = _restore_name(ex.summary_en, elder, "en")
        ex.summary_yue = _restore_name(ex.summary_yue, elder, "yue")
        payload = ex.model_dump()
        row = db.exec(select(CallSummary).where(CallSummary.call_id == call_id)).first()
        model = f"{providers.extractor.name}:{getattr(providers.extractor, 'model', 'rules')}"
        if row is None:
            row = CallSummary(
                call_id=call_id,
                extraction=payload,
                summary_yue=ex.summary_yue,
                summary_en=ex.summary_en,
                overall_confidence=ex.overall_confidence,
                model_used=model,
            )
        else:
            row.extraction, row.summary_en, row.summary_yue = payload, ex.summary_en, ex.summary_yue
            row.overall_confidence, row.model_used = ex.overall_confidence, model
        db.add(row)
        db.commit()
        saved = memory.save_facts(
            db, elder_id, call_id, [f.model_dump() for f in ex.new_memory_facts]
        )
        nudge = db.exec(select(Nudge).where(Nudge.call_id == call_id)).first()
        if nudge and ex.nudge_response in ("interested", "declined", "unclear"):
            nudge.elder_response = ex.nudge_response
            db.add(nudge)
            db.commit()
        update_followups(db, elder_id)
        result = risk.assess(db, elder_id)
        await sync_unreachable_alert(db, elder_id, result)
        outcome = {
            "call_id": call_id,
            "elder_id": elder_id,
            "level": result.level,
            "reason_codes": [r["code"] for r in result.reasons],
            "facts_saved": len(saved),
            "notes": notes,
        }

    await hub.broadcast({"type": "call.summary_ready", "call_id": call_id, "elder_id": elder_id})
    await hub.broadcast(
        {
            "type": "risk.updated",
            "elder_id": elder_id,
            "level": result.level,
            "reasons": [r["code"] for r in result.reasons],
        }
    )
    logger.info("post-call analysis done: %s", outcome)
    return outcome


def nudge_turn_index(nudge: Nudge | None, rows: list[Turn]) -> int | None:
    """Index of the Kaifong turn that made the nudge (its stage is stored with the turn)."""
    if nudge is None:
        return None
    return next(
        (
            t.index
            for t in rows
            if t.speaker == "kaifong" and t.latency_breakdown.get("stage") == "nudge"
        ),
        None,
    )
