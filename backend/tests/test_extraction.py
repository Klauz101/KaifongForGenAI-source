"""Post-call extraction: schema validation, retry, evidence enforcement, offline extractor, memory."""

import asyncio
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from app.analysis import memory
from app.analysis.extraction import enforce_evidence, extract_call, fallback_extraction
from app.analysis.mock_extractor import extract as mock_extract
from app.models import Elder, MemoryFact, utcnow
from app.providers.base import LLMProvider, LLMResult
from app.schemas import Extraction
from app.security.pseudonymize import Pseudonymizer

pytestmark = pytest.mark.usefixtures("reseed_after")

PSEUD = Pseudonymizer("Chan Demo Mei", "9123 4567", ("Mrs Chan", "陳婆婆"))


def turns(*elder_lines: str) -> list[dict]:
    out = [{"index": 0, "speaker": "kaifong", "text": "早晨呀！今日食咗飯未呀？"}]
    for i, text in enumerate(elder_lines):
        out.append({"index": 2 * i + 1, "speaker": "elder", "text": text})
        out.append({"index": 2 * i + 2, "speaker": "kaifong", "text": "明白。"})
    return out


# ---------- offline extractor (mock mode) -----------------------------------------------------


def test_skipped_lunch_and_not_going_out_are_extracted_with_evidence() -> None:
    t = turns("冇食午飯，唔係好有胃口", "冇出街，膝頭哥好痛", "我孫女星期日嚟探我")
    x = Extraction.model_validate(mock_extract(t))
    assert x.appetite.value == "skipped_meals" and x.appetite.evidence_turns == [1]
    assert x.went_out_today.value == "no" and x.went_out_today.evidence_turns == [3]
    assert x.pain.value == "severe" and x.pain.location == "knee" and x.pain.evidence_turns == [3]
    assert x.sleep.value == "unknown" and x.sleep.evidence_turns == []  # not stated: unknown
    assert any(f.category == "event" for f in x.new_memory_facts)
    assert any(f.category == "health" for f in x.new_memory_facts)
    assert x.summary_en and x.summary_yue and "meal" in x.summary_en


def test_english_call_and_negation_traps() -> None:
    t = turns("I didn't eat lunch today", "I slept well and took my medicine", "I went to the park")
    x = Extraction.model_validate(mock_extract(t))
    assert (x.appetite.value, x.sleep.value, x.meds_taken.value, x.went_out_today.value) == (
        "skipped_meals",
        "good",
        "yes",
        "yes",
    )
    # "冇食藥" (didn't take medicine) must not be read as skipping a meal
    y = Extraction.model_validate(mock_extract(turns("冇食藥", "食咗早餐")))
    assert y.meds_taken.value == "no" and y.appetite.value == "normal"


def test_only_the_older_persons_words_count() -> None:
    t = [
        {"index": 0, "speaker": "kaifong", "text": "你有冇出街呀？瞓得好唔好？"},
        {"index": 1, "speaker": "elder", "text": "嗯"},
    ]
    x = Extraction.model_validate(mock_extract(t))
    assert (
        x.went_out_today.value == "unknown"
        and x.sleep.value == "unknown"
        and x.overall_confidence == "low"
    )


def test_nudge_response_is_read_from_the_turn_after_the_nudge() -> None:
    t = turns("早晨", "好呀，我試下")
    assert mock_extract(t, nudge_turn=2)["nudge_response"] == "interested"
    assert mock_extract(turns("早晨", "唔使喇"), nudge_turn=2)["nudge_response"] == "declined"
    assert mock_extract(t)["nudge_response"] == "not_offered"


def test_loneliness_and_confusion_cues() -> None:
    x = Extraction.model_validate(
        mock_extract(turns("有啲孤單，成日都得我一個", "咦，你頭先問過我食咗飯未？"))
    )
    assert x.loneliness_expressed.value is True and x.mood.value == 2
    assert x.confusion_cues.value == ["repeated_question"]


# ---------- LLM path: validation, retry, fallback, evidence -----------------------------------


class ScriptedLLM(LLMProvider):
    name = "scripted"
    streams = False

    def __init__(self, replies: list[str]) -> None:
        self.replies, self.calls = replies, []

    async def complete(self, system, messages, *, temperature=0.6, json_mode=False, hint=None):
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "temperature": temperature,
                "json_mode": json_mode,
            }
        )
        return LLMResult(text=self.replies[min(len(self.calls) - 1, len(self.replies) - 1)])


GOOD = json.dumps(
    {
        "mood": {"value": 3, "confidence": "medium", "evidence_turns": [1]},
        "appetite": {"value": "skipped_meals", "confidence": "high", "evidence_turns": [1]},
        "summary_en": "Skipped lunch.",
        "summary_yue": "冇食午飯。",
        "overall_confidence": "medium",
    }
)


def run_extract(llm, t=None):
    return asyncio.run(extract_call(llm, t or turns("冇食午飯"), PSEUD, "yue"))


def test_valid_json_passes_first_time_at_temperature_zero() -> None:
    llm = ScriptedLLM([GOOD])
    ex, _ = run_extract(llm)
    assert ex.appetite.value == "skipped_meals" and len(llm.calls) == 1
    assert llm.calls[0]["temperature"] == 0.0 and llm.calls[0]["json_mode"] is True


def test_invalid_json_is_retried_once_with_the_validation_error() -> None:
    llm = ScriptedLLM(['{"mood": {"value": 9}}', GOOD])
    ex, _ = run_extract(llm)
    assert len(llm.calls) == 2 and ex.appetite.value == "skipped_meals"
    retry = llm.calls[1]["messages"]
    assert "invalid" in retry[-1]["content"].lower() and "mood" in retry[-1]["content"]


def test_still_invalid_after_retry_stores_low_confidence_all_unknown() -> None:
    llm = ScriptedLLM(["not json at all"])
    ex, notes = run_extract(llm)
    assert len(llm.calls) == 2 and notes == ["fallback:all_unknown"]
    assert (
        ex.overall_confidence == "low" and ex.mood.value is None and ex.appetite.value == "unknown"
    )
    assert ex.summary_en and ex.summary_yue


def test_provider_failure_does_not_lose_the_call() -> None:
    class Down(LLMProvider):
        name = "down"
        streams = False

        async def complete(self, *a, **k):
            raise RuntimeError("network down")

    ex, notes = run_extract(Down())
    assert ex.overall_confidence == "low" and notes == ["fallback:all_unknown"]


def test_fenced_json_is_accepted() -> None:
    ex, _ = run_extract(ScriptedLLM([f"```json\n{GOOD}\n```"]))
    assert ex.appetite.value == "skipped_meals"


def test_transcript_sent_to_the_llm_is_pseudonymised() -> None:
    llm = ScriptedLLM([GOOD])
    t = turns("My name is Chan Demo Mei, phone 9123 4567, ID A123456(7), I skipped lunch")
    run_extract(llm, t)
    sent = json.dumps(llm.calls[0]["messages"])
    assert (
        "Chan Demo" not in sent
        and "9123" not in sent
        and "A123456" not in sent
        and "[ELDER]" in sent
    )


def test_values_without_valid_evidence_revert_to_unknown() -> None:
    x = Extraction.model_validate(
        {
            "mood": {"value": 4, "evidence_turns": []},
            "sleep": {"value": "poor", "evidence_turns": [99]},  # turn does not exist
            "appetite": {
                "value": "normal",
                "evidence_turns": [0],
            },  # turn 0 is Kaifong, not the person
            "pain": {"value": "severe", "location": "knee", "evidence_turns": []},
            "went_out_today": {"value": "yes", "evidence_turns": [1]},  # valid
            "loneliness_expressed": {"value": True, "evidence_turns": []},
            "confusion_cues": {"value": ["lost_thread"], "evidence_turns": []},
            "social_contact": {"value": ["friend"], "evidence_turns": []},
        }
    )
    fixed, notes = enforce_evidence(x, elder_turns={1, 3})
    assert (
        fixed.mood.value is None
        and fixed.sleep.value == "unknown"
        and fixed.appetite.value == "unknown"
    )
    assert fixed.pain.value == "unknown" and fixed.pain.location is None
    assert fixed.went_out_today.value == "yes" and fixed.went_out_today.evidence_turns == [1]
    assert fixed.loneliness_expressed.value is False and fixed.confusion_cues.value == ["none"]
    assert fixed.social_contact.value == ["none_mentioned"] and "mood" in notes


def test_diagnoses_are_stripped_never_stored() -> None:
    x = Extraction.model_validate(
        {
            "concerns": ["Possible dementia", "Skipped a meal"],
            "new_memory_facts": [
                {"fact_en": "May have depression", "fact_yue": "可能有抑鬱", "category": "health"}
            ],
            "summary_en": "Skipped lunch. This suggests early dementia.",
            "summary_yue": "冇食午飯。",
        }
    )
    fixed, _ = enforce_evidence(x, set())
    assert fixed.concerns == ["Skipped a meal"] and fixed.new_memory_facts == []
    assert "dementia" not in fixed.summary_en and "Skipped lunch." in fixed.summary_en


def test_extraction_schema_rejects_out_of_range_values() -> None:
    with pytest.raises(ValidationError):
        Extraction.model_validate({"mood": {"value": 7}})
    with pytest.raises(ValidationError):
        Extraction.model_validate({"appetite": {"value": "starving"}})
    assert fallback_extraction().overall_confidence == "low"


# ---------- memory -----------------------------------------------------------------------------


def fact(en: str, cat="event", days=None) -> dict:
    return {"fact_en": en, "fact_yue": en, "category": cat, "expires_in_days": days}


def test_memory_dedupes_near_duplicates_and_expires_events(db: Session) -> None:
    elder = db.exec(select(Elder).where(Elder.display_name_en == "Mr Lau")).first()
    saved = memory.save_facts(
        db, elder.id, 1, [fact("A visit from their granddaughter is coming up", days=7)]
    )
    assert len(saved) == 1
    again = memory.save_facts(
        db, elder.id, 2, [fact("A visit from their granddaughter is coming up soon", days=7)]
    )
    assert again == []  # near-duplicate skipped
    later = utcnow() + timedelta(days=8)
    assert memory.expire_facts(db, elder.id, now=later) == 1
    db.expire_all()
    assert db.get(MemoryFact, saved[0].id).active is False
    assert all(
        "granddaughter" not in f["en"] for f in memory.retrieve_facts(db, elder.id, now=later)
    )


def test_retrieval_is_capped_at_five_and_prefers_recent_events_and_health(db: Session) -> None:
    elder = db.exec(select(Elder).where(Elder.display_name_en == "Mrs Wong")).first()
    routines = [
        "Waters the balcony plants every morning",
        "Prefers tea without sugar",
        "Walks to the wet market on Mondays",
        "Listens to the radio after dinner",
        "Folds laundry on Sundays",
        "Calls her sister on Thursdays",
    ]
    for text in routines:
        memory.save_facts(db, elder.id, 1, [fact(text, cat="routine")])
    memory.save_facts(db, elder.id, 1, [fact("Doctor appointment on Friday", cat="event", days=5)])
    got = memory.retrieve_facts(db, elder.id)
    assert len(got) == 5 and got[0]["en"] == "Doctor appointment on Friday"
    for f in db.exec(
        select(MemoryFact).where(MemoryFact.elder_id == elder.id, MemoryFact.source_call_id == 1)
    ):
        db.delete(f)
    db.commit()
