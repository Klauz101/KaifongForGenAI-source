"""Triage evaluation: do the risk rules give the expected Green/Amber/Red on 10 synthetic histories?

Reports "x/10 correct" exactly as the deck's prototype slide asks, with every disagreement explained.
IMPORTANT: the expected levels are proposed by the developers and must be agreed by the team (see
triage_cases.yaml). Run: `cd backend && .venv/bin/python -m eval.eval_triage`.
"""

from datetime import date, timedelta

from app.analysis.risk import CallRecord, evaluate
from app.schemas import Extraction
from eval.common import EVAL_DIR, load_yaml

AS_OF = date(2026, 1, 15)
DEFAULT_DAY = {
    "mood": 4,
    "sleep": "good",
    "appetite": "normal",
    "meds": "yes",
    "pain": "none",
    "went_out": "yes",
    "lonely": False,
    "confusion": ["none"],
    "conf": "high",
}


def _extraction(d: dict) -> dict:
    return Extraction.model_validate(
        {
            "mood": {"value": d["mood"]},
            "sleep": {"value": d["sleep"]},
            "appetite": {"value": d["appetite"]},
            "meds_taken": {"value": d["meds"]},
            "pain": {"value": d["pain"]},
            "went_out_today": {"value": d["went_out"]},
            "loneliness_expressed": {"value": d["lonely"]},
            "confusion_cues": {"value": d["confusion"]},
            "overall_confidence": d["conf"],
        }
    ).model_dump()


def history(case: dict) -> list[CallRecord]:
    base = {**DEFAULT_DAY, **case.get("base", {})}
    records = []
    for days_ago in range(20, -1, -1):
        day = AS_OF - timedelta(days=days_ago)
        if days_ago in case.get("no_answer", []):
            records.append(CallRecord(100 + days_ago, day, "no_answer"))
            continue
        d = {**base, **case.get("overrides", {}).get(days_ago, {})}
        records.append(CallRecord(100 + days_ago, day, "completed", _extraction(d), d["conf"]))
    return records


def run() -> dict:
    spec = load_yaml(EVAL_DIR / "triage_cases.yaml")
    rows = []
    for case in spec["cases"]:
        result = evaluate(history(case), AS_OF)
        rows.append(
            {
                "id": case["id"],
                "title": case["title"],
                "expected": case["expected"],
                "got": result.level,
                "correct": result.level == case["expected"],
                "reasons": [r["code"] for r in result.reasons],
                "debated": case.get("debated", False),
                "rationale": case["rationale"],
            }
        )
    correct = sum(r["correct"] for r in rows)
    return {
        "correct": correct,
        "total": len(rows),
        "summary": f"{correct}/{len(rows)} correct",
        "agreed_by_team": bool(spec.get("agreed_by_team")),
        "disagreements": [r for r in rows if not r["correct"]],
        "rows": rows,
    }


if __name__ == "__main__":
    out = run()
    print(out["summary"], "(expected levels agreed by team: {})".format(out["agreed_by_team"]))
    for r in out["rows"]:
        print(
            f"{'ok ' if r['correct'] else 'XX '}{r['id']} expected={r['expected']} got={r['got']} {r['reasons']}"
        )
