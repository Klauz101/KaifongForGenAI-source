"""Red-flag detector evaluation: recall and precision on scripted cases (SPEC 15 Phase 7).

Run: `cd backend && .venv/bin/python -m eval.eval_redflags`. Deterministic and offline.
Gating number: core recall must be 100% (a missed emergency phrase is a safety failure). The `stretch`
group is reported separately and never used to tune the phrase list.
"""

from collections import defaultdict

from app.safety.redflags import detect
from eval.common import EVAL_DIR, load_yaml, pct


def run() -> dict:
    cases = load_yaml(EVAL_DIR / "redflag_cases.yaml")["cases"]
    rows = []
    for c in cases:
        found = detect(c["text"]).categories
        expected = set(c["expected"])
        rows.append(
            {
                **c,
                "predicted": found,
                "alerted": bool(found),
                "alert_ok": bool(found)
                == bool(expected),  # did it alert when it should (and not otherwise)?
                "category_ok": expected <= set(found) if expected else not found,
            }
        )

    def group(name: str) -> list[dict]:
        return [r for r in rows if r["group"] == name]

    core, stretch = group("core"), group("stretch")
    non_stretch = [r for r in rows if r["group"] != "stretch"]
    tp = sum(1 for r in non_stretch if r["expected"] and r["alerted"])
    fp = sum(1 for r in non_stretch if not r["expected"] and r["alerted"])
    fn = sum(1 for r in non_stretch if r["expected"] and not r["alerted"])
    per_category: dict[str, list[bool]] = defaultdict(list)
    per_lang: dict[str, list[bool]] = defaultdict(list)
    for r in core:
        for cat in r["expected"]:
            per_category[cat].append(cat in r["predicted"])
        per_lang[r["lang"]].append(r["alerted"])
    neg = group("negative") + group("negation")
    return {
        "cases_total": len(rows),
        "counts": {g: len(group(g)) for g in ("core", "negation", "negative", "stretch")},
        "negatives_total": len(neg),
        "core_recall_pct": pct(sum(r["alerted"] for r in core), len(core)),
        "core_category_accuracy_pct": pct(sum(r["category_ok"] for r in core), len(core)),
        "core_recall_by_category": {
            k: pct(sum(v), len(v)) for k, v in sorted(per_category.items())
        },
        "core_recall_by_language": {k: pct(sum(v), len(v)) for k, v in sorted(per_lang.items())},
        "precision_pct": pct(tp, tp + fp),
        "recall_pct": pct(
            tp, tp + fn
        ),  # over core + negation positives (self-harm negation exceptions)
        "negatives_correct_pct": pct(sum(r["alert_ok"] for r in neg), len(neg)),
        "false_alarms": [
            {"id": r["id"], "text": r["text"], "predicted": r["predicted"]}
            for r in neg
            if not r["alert_ok"]
        ],
        "core_misses": [
            {"id": r["id"], "text": r["text"], "expected": r["expected"]}
            for r in core
            if not r["alerted"]
        ],
        "stretch_recall_pct": pct(sum(r["alerted"] for r in stretch), len(stretch)),
        "stretch_misses": [
            {"id": r["id"], "text": r["text"], "expected": r["expected"]}
            for r in stretch
            if not r["alerted"]
        ],
        "rows": rows,
    }


if __name__ == "__main__":
    out = run()
    print({k: v for k, v in out.items() if k not in ("rows",)})
