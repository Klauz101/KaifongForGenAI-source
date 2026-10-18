"""Post-call extraction (SPEC 7.4): transcript -> validated JSON with evidence, via the LLM.

Temperature 0. The transcript is pseudonymised before it leaves. Output is validated against
`Extraction`; one retry with the validation error; then a safe all-unknown, low-confidence fallback.
Afterwards every value is checked against the transcript: no evidence -> `unknown`.
"""

import json
import logging
import re

from pydantic import ValidationError

from app.analysis.summaries import NO_DATA
from app.providers.base import LLMProvider
from app.schemas import Extraction
from app.security.pseudonymize import Pseudonymizer

logger = logging.getLogger(__name__)

DIAGNOSES = re.compile(
    r"dementia|alzheimer|depress|parkinson|stroke|diagnos|認知障礙|腦退化|抑鬱|柏金遜|中風|焦慮症",
    re.IGNORECASE,
)

SYSTEM = """You turn the transcript of a short phone call between Kaifong (an AI helper) and an older person into a structured wellbeing record for a social worker.

Rules:
- Output ONLY one JSON object, no prose, matching the schema below.
- Use "unknown" (or null for mood) whenever something is not clearly stated by the OLDER PERSON. Never guess.
- Never infer or state a diagnosis (no dementia, depression, etc.). Only record what was said or observed in the words.
- Every value that is not unknown MUST cite "evidence_turns": the [index] numbers of the older person's lines that support it.
- mood is 1 (very low) to 5 (very good), or null. confidence is high, medium or low.
- "concerns": short English strings. "summary_en": 2-3 plain sentences for a social worker. "summary_yue": the same in spoken-style Traditional Chinese (粵語).
- "new_memory_facts": things worth remembering for next time (family visits, events, health complaints), each with fact_en, fact_yue, category (health|family|routine|interest|event|concern), expires_in_days (integer or null).
- "nudge_response": how the person answered Kaifong's suggestion to connect with people (interested|declined|unclear), or not_offered.
- [ELDER] stands for the person's name; keep it as is.

Schema (field: allowed values):
{schema}
"""

SCHEMA_HINT = """{
 "mood": {"value": 1-5|null, "confidence": "high|medium|low", "evidence_turns": [int]},
 "sleep": {"value": "good|poor|unknown", "confidence": "...", "evidence_turns": []},
 "appetite": {"value": "normal|reduced|skipped_meals|unknown", "confidence": "...", "evidence_turns": []},
 "meds_taken": {"value": "yes|partial|no|unknown|not_applicable", "confidence": "...", "evidence_turns": []},
 "pain": {"value": "none|mild|moderate|severe|unknown", "location": "string|null", "evidence_turns": []},
 "went_out_today": {"value": "yes|no|unknown", "evidence_turns": []},
 "social_contact": {"value": ["family_call","friend","neighbour","centre","none_mentioned"], "evidence_turns": []},
 "loneliness_expressed": {"value": true|false, "evidence_turns": []},
 "confusion_cues": {"value": ["repeated_question","wrong_day_or_date","lost_thread","none"], "evidence_turns": []},
 "concerns": ["..."],
 "new_memory_facts": [{"fact_en": "...", "fact_yue": "...", "category": "...", "expires_in_days": int|null}],
 "nudge_response": "interested|declined|unclear|not_offered",
 "summary_en": "...", "summary_yue": "...",
 "overall_confidence": "high|medium|low"
}"""


def fallback_extraction() -> Extraction:
    """All fields unknown, low confidence (used when the model cannot produce valid JSON)."""
    return Extraction(
        summary_en=NO_DATA["en"], summary_yue=NO_DATA["yue"], overall_confidence="low"
    )


def _json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1] if start != -1 and end > start else text)


def enforce_evidence(ex: Extraction, elder_turns: set[int]) -> tuple[Extraction, list[str]]:
    """Drop invalid evidence indices; a value with no valid evidence reverts to unknown. Returns notes."""
    notes: list[str] = []

    def clean(field_name: str, field) -> bool:
        field.evidence_turns = sorted({i for i in field.evidence_turns if i in elder_turns})
        if not field.evidence_turns:
            notes.append(field_name)
            return False
        return True

    if ex.mood.value is not None and not clean("mood", ex.mood):
        ex.mood.value, ex.mood.confidence = None, "low"
    for name, field, unknown in (
        ("sleep", ex.sleep, "unknown"),
        ("appetite", ex.appetite, "unknown"),
        ("meds_taken", ex.meds_taken, "unknown"),
        ("pain", ex.pain, "unknown"),
        ("went_out_today", ex.went_out_today, "unknown"),
    ):
        if field.value not in ("unknown", "not_applicable") and not clean(name, field):
            field.value = unknown
            if name == "pain":
                field.location = None
    if ex.social_contact.value != ["none_mentioned"] and not clean(
        "social_contact", ex.social_contact
    ):
        ex.social_contact.value = ["none_mentioned"]
    if ex.loneliness_expressed.value and not clean("loneliness_expressed", ex.loneliness_expressed):
        ex.loneliness_expressed.value = False
    if ex.confusion_cues.value != ["none"] and not clean("confusion_cues", ex.confusion_cues):
        ex.confusion_cues.value = ["none"]
    # No diagnoses, ever: drop any concern/summary sentence that names one.
    ex.concerns = [c for c in ex.concerns if not DIAGNOSES.search(c)]
    ex.new_memory_facts = [
        f for f in ex.new_memory_facts if not DIAGNOSES.search(f.fact_en + f.fact_yue)
    ]
    for attr in ("summary_en", "summary_yue"):
        text = getattr(ex, attr)
        if DIAGNOSES.search(text):
            sentences = [
                s for s in re.split(r"(?<=[.!?。])\s*", text) if s and not DIAGNOSES.search(s)
            ]
            setattr(ex, attr, " ".join(sentences))
            notes.append(f"{attr}:diagnosis_removed")
    return ex, notes


def transcript_text(turns: list[dict], pseud: Pseudonymizer) -> str:
    lines = []
    for t in turns:
        who = "OLDER PERSON" if t["speaker"] == "elder" else "KAIFONG"
        lines.append(f"[{t['index']}] {who}: {pseud.scrub(t['text'])}")
    return "\n".join(lines)


async def extract_call(
    llm: LLMProvider,
    turns: list[dict],
    pseud: Pseudonymizer,
    language: str,
    nudge_turn: int | None = None,
) -> tuple[Extraction, list[str]]:
    """Run extraction with validation + one retry. `turns`: [{index, speaker, text}]."""
    elder_turns = {t["index"] for t in turns if t["speaker"] == "elder"}
    prompt = transcript_text(turns, pseud)
    if nudge_turn is not None:
        prompt += f"\n\n(Kaifong suggested connecting with people at turn [{nudge_turn}].)"
    messages = [{"role": "user", "content": prompt}]
    system = SYSTEM.format(schema=SCHEMA_HINT)
    hint = {"task": "extract", "turns": turns, "language": language, "nudge_turn": nudge_turn}
    raw = ""
    for attempt in range(2):
        try:
            raw = (
                await llm.complete(system, messages, temperature=0.0, json_mode=True, hint=hint)
            ).text
            ex = Extraction.model_validate(_json_object(raw))
            ex, notes = enforce_evidence(ex, elder_turns)
            return ex, notes
        except (ValidationError, ValueError) as exc:
            error = str(exc)[:600]
            logger.warning("extraction attempt %d invalid: %s", attempt + 1, error)
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": f"That JSON was invalid: {error}\nReturn corrected JSON only.",
                },
            ]
        except Exception:
            logger.exception("extraction call failed")
            break
    return fallback_extraction(), ["fallback:all_unknown"]
