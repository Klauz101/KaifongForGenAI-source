"""Extraction evaluation: per-field agreement with hand-written gold on 30 synthetic transcripts.

Uses the SAME code path as production (`extract_call`), with whichever extractor is configured:
the offline rule-based extractor in mock mode, or the real LLM when keys are set. Run:
`cd backend && .venv/bin/python -m eval.eval_extraction`.
Target: >= 85% agreement. The gold set deliberately includes paraphrases the mock extractor was not
designed for, so a mock-mode score is a floor for the rules, not a claim about a real model.
"""

import asyncio
from collections import defaultdict

from app.analysis.extraction import extract_call
from app.providers.registry import get_providers
from app.schemas import Extraction
from app.security.pseudonymize import Pseudonymizer
from eval.common import EVAL_DIR, load_yaml, pct

FIELDS = [
    "mood",
    "sleep",
    "appetite",
    "meds_taken",
    "pain",
    "went_out_today",
    "social_contact",
    "loneliness_expressed",
    "confusion_cues",
]
DEFAULTS = {
    "mood": None,
    "sleep": "unknown",
    "appetite": "unknown",
    "meds_taken": "unknown",
    "pain": "unknown",
    "went_out_today": "unknown",
    "social_contact": ["none_mentioned"],
    "loneliness_expressed": False,
    "confusion_cues": ["none"],
}
NEUTRAL = {
    "yue": ["你好", "嗯，繼續講"],
    "en": ["Hello", "Mm, go on"],
}  # Kaifong lines carry no keywords


def gold_for(case: dict) -> dict:
    return {f: case["gold"].get(f, DEFAULTS[f]) for f in FIELDS}


def predicted(ex: Extraction) -> dict:
    return {f: getattr(ex, f).value for f in FIELDS}


def agrees(field: str, gold, pred) -> bool:
    if field == "mood":  # subjective: within one point counts; both unknown counts
        return (gold is None and pred is None) or (
            gold is not None and pred is not None and abs(gold - pred) <= 1
        )
    if isinstance(gold, list):
        return set(gold) == set(pred)
    return gold == pred


def build_turns(case: dict) -> list[dict]:
    k = NEUTRAL[case["lang"]]
    turns = [{"index": 0, "speaker": "kaifong", "text": k[0]}]
    for text in case["turns"]:
        turns.append({"index": len(turns), "speaker": "elder", "text": text})
        turns.append({"index": len(turns), "speaker": "kaifong", "text": k[1]})
    return turns


async def _run_all(cases: list[dict]) -> list[tuple[dict, Extraction, list[dict]]]:
    llm = get_providers().extractor
    out = []
    for case in cases:
        turns = build_turns(case)
        pseud = Pseudonymizer("Eval Person", "00000000", ("Mrs Eval",))
        ex, _ = await extract_call(llm, turns, pseud, case["lang"])
        out.append((case, ex, turns))
    return out


def run() -> dict:
    cases = load_yaml(EVAL_DIR / "extraction_gold" / "cases.yaml")["cases"]
    results = asyncio.run(_run_all(cases))
    per_field: dict[str, list[bool]] = defaultdict(list)
    per_tag: dict[str, list[bool]] = defaultdict(list)
    per_lang: dict[str, list[bool]] = defaultdict(list)
    wrong: list[dict] = []
    halluc = value_fields = evidence_ok = evidence_total = 0
    for case, ex, turns in results:
        gold, pred = gold_for(case), predicted(ex)
        elder_idx = {t["index"] for t in turns if t["speaker"] == "elder"}
        for f in FIELDS:
            ok = agrees(f, gold[f], pred[f])
            per_field[f].append(ok)
            per_tag[case["tag"]].append(ok)
            per_lang[case["lang"]].append(ok)
            if not ok:
                wrong.append(
                    {
                        "case": case["id"],
                        "tag": case["tag"],
                        "field": f,
                        "gold": gold[f],
                        "predicted": pred[f],
                        "text": " / ".join(case["turns"]),
                    }
                )
            gold_blank = agrees(f, DEFAULTS[f], gold[f]) if f != "mood" else gold[f] is None
            pred_blank = pred[f] == DEFAULTS[f] if f != "mood" else pred[f] is None
            if gold_blank and not pred_blank:
                halluc += 1  # said something the person never said
            if not pred_blank:  # every value-bearing answer must cite the person's own turns
                value_fields += 1
                ev = getattr(ex, f).evidence_turns
                evidence_total += 1
                evidence_ok += bool(ev) and set(ev) <= elder_idx
    flat = [x for v in per_field.values() for x in v]
    llm = get_providers().extractor
    return {
        "provider": llm.name,
        "model": getattr(llm, "model", "rules (offline)"),
        "cases": len(cases),
        "fields_per_case": len(FIELDS),
        "agreement_pct": pct(sum(flat), len(flat)),
        "by_field_pct": {f: pct(sum(v), len(v)) for f, v in per_field.items()},
        "by_tag_pct": {t: pct(sum(v), len(v)) for t, v in sorted(per_tag.items())},
        "by_language_pct": {t: pct(sum(v), len(v)) for t, v in sorted(per_lang.items())},
        "unsupported_values": halluc,
        "unsupported_value_note": "fields filled in when the person said nothing about them",
        "evidence_valid_pct": pct(evidence_ok, evidence_total),
        "disagreements": wrong,
    }


if __name__ == "__main__":
    out = run()
    print({k: v for k, v in out.items() if k != "disagreements"})
    for w in out["disagreements"]:
        print(w)
