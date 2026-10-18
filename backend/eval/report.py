"""Run every evaluation and write eval/results.md + eval/results.json (shown on the Metrics page).

Run: `make eval` (offline: red flags, extraction, triage, STT clips if any, latency from this database).
Targets (SPEC 15 Phase 7): red-flag recall 100%, extraction agreement >= 85%, median reply latency < 3 s.
Anything not measured says "not yet measured". Nothing is rounded up or tuned.
"""

import json
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.analysis import cost
from app.conversation.service import latency_stats
from app.db import engine, init_db
from app.models import Call, Turn
from app.providers.registry import get_providers
from eval import eval_extraction, eval_redflags, eval_stt, eval_triage
from eval.common import EVAL_DIR, SYNTHETIC_NOTE


def live_measurements() -> dict:
    """Reply latency and cost from live (non-seed) calls recorded in this database."""
    init_db()
    with Session(engine) as db:
        rows = db.exec(
            select(Turn, Call)
            .join(Call, Turn.call_id == Call.id)
            .where(Call.mode != "seed", Turn.speaker == "kaifong")
        ).all()
        ttfa = [
            t.latency_breakdown["ttfa_ms"]
            for t, _ in rows
            if t.latency_breakdown.get("ttfa_ms") is not None
        ]
        usages = [
            c.usage
            for c in db.exec(select(Call).where(Call.mode != "seed", Call.status != "in_progress"))
            if c.usage
        ]
    providers = get_providers().names()
    mock_only = all(v == "mock" for v in providers.values())
    return {
        "providers": providers,
        "mock_only": mock_only,
        "latency": latency_stats(ttfa),
        "cost": cost.summarise(usages),
    }


def run_all() -> dict:
    flags, extraction, triage, stt, live = (
        eval_redflags.run(),
        eval_extraction.run(),
        eval_triage.run(),
        eval_stt.run(),
        live_measurements(),
    )
    median = live["latency"]["median_ms"]
    targets = [
        {
            "name": "Red-flag recall on core phrases",
            "target": "100%",
            "value": f"{flags['core_recall_pct']}%",
            "met": flags["core_recall_pct"] == 100.0,
        },
        {
            "name": "Extraction agreement with gold",
            "target": ">= 85%",
            "value": f"{extraction['agreement_pct']}%",
            "met": extraction["agreement_pct"] >= 85.0,
        },
        {
            "name": "Median reply latency",
            "target": "< 3000 ms",
            "value": (
                "not yet measured"
                if median is None
                else f"{median} ms"
                + (" (mock providers: app overhead only)" if live["mock_only"] else "")
            ),
            "met": None if median is None or live["mock_only"] else median < 3000,
        },
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "synthetic_note": SYNTHETIC_NOTE,
        "targets": targets,
        "redflags": flags,
        "extraction": extraction,
        "triage": triage,
        "stt": stt,
        "live": live,
    }


def _mark(met: bool | None) -> str:
    return "met" if met else "NOT met" if met is False else "not measurable yet"


def to_markdown(r: dict) -> str:
    f, x, t, s, live = r["redflags"], r["extraction"], r["triage"], r["stt"], r["live"]
    L = [
        "# Evaluation results",
        "",
        f"_Generated {r['generated_at']} by `make eval`._",
        "",
        f"> **{r['synthetic_note']}**",
        "",
        "## Targets",
        "",
        "| Measure | Target | Result | Status |",
        "|---|---|---|---|",
    ]
    L += [
        f"| {g['name']} | {g['target']} | {g['value']} | {_mark(g['met'])} |" for g in r["targets"]
    ]
    L += [
        "",
        "## Red-flag detector",
        "",
        (
            f"{f['cases_total']} scripted cases ({f['counts']['core']} core, {f['counts']['negation']} negation, "
            f"{f['counts']['negative']} ordinary talk, {f['counts']['stretch']} stretch)."
        ),
        "",
        (
            f"- **Core recall: {f['core_recall_pct']}%** (category correct {f['core_category_accuracy_pct']}%). "
            f"By language: {f['core_recall_by_language']}. By category: {f['core_recall_by_category']}."
        ),
        (
            f"- **Precision: {f['precision_pct']}%**, recall {f['recall_pct']}% (core + negation cases). "
            f"Negation and ordinary talk handled correctly: {f['negatives_correct_pct']}% of {f['negatives_total']}."
        ),
        (
            f"- **Stretch recall: {f['stretch_recall_pct']}%.** These are realistic phrasings the phrase list was *not* built around. "
            "A low number here is the real warning: **the deterministic list alone will miss unlisted wording.**"
        ),
        "",
    ]
    if f["core_misses"]:
        L += (
            ["Core misses (safety failures):", ""]
            + [f"- {m['id']}: {m['text']} (expected {m['expected']})" for m in f["core_misses"]]
            + [""]
        )
    if f["false_alarms"]:
        L += (
            ["False alarms:", ""]
            + [f"- {m['id']}: {m['text']} → {m['predicted']}" for m in f["false_alarms"]]
            + [""]
        )
    L += ["Stretch misses (not used to tune the list):", ""] + [
        f"- {m['id']}: {m['text']} (expected {m['expected']})" for m in f["stretch_misses"]
    ]
    L += [
        "",
        (
            "The optional LLM classifier (`SAFETY_LLM_CLASSIFIER`) could catch some of these but is **not measured**: it needs a real LLM. "
            "Core cases use the phrases the list was built from, so a 100% core score shows the code works as designed, not that coverage is complete."
        ),
        "",
        "## Extraction",
        "",
        f"Extractor: `{x['provider']}` / `{x['model']}`. {x['cases']} transcripts × {x['fields_per_case']} fields.",
        "",
        f"- **Agreement with gold: {x['agreement_pct']}%.** By tag: {x['by_tag_pct']}. By language: {x['by_language_pct']}.",
        f"- Per field: {x['by_field_pct']}.",
        f"- Value-bearing answers citing the person's own turns: {x['evidence_valid_pct']}%.",
        f"- Unsupported values ({x['unsupported_value_note']}): {x['unsupported_values']}.",
        "",
    ]
    L += ["Disagreements:", ""] + [
        f"- {d['case']} ({d['tag']}) {d['field']}: gold {d['gold']!r}, got {d['predicted']!r}; _{d['text']}_"
        for d in x["disagreements"]
    ]
    L += [
        "",
        (
            "The `hard` cases are paraphrases the offline keyword extractor was not designed for; in mock mode they show its floor. "
            "A real LLM has not been measured here (no keys)."
        ),
        "",
        "## Triage rules",
        "",
        f"**{t['summary']}** on {t['total']} synthetic histories. "
        + (
            "Expected levels agreed by the team."
            if t["agreed_by_team"]
            else "**Expected levels are the developers' proposal and are NOT yet agreed by the team**; agree or change each row in `triage_cases.yaml` before quoting this."
        ),
        "",
    ]
    L += ["| Case | Expected | Got | Reasons | |", "|---|---|---|---|---|"]
    L += [
        f"| {c['title']} | {c['expected']} | {c['got']} | {', '.join(c['reasons']) or '-'} | {'' if c['correct'] else 'MISMATCH'}{' (debated)' if c['debated'] else ''} |"
        for c in t["rows"]
    ]
    L += ["", "## Speech recognition (character error rate)", ""]
    L += (
        [f"**CER {s['cer_pct']}%** over {s['clips']} consented clips with `{s['provider']}`."]
        if s["status"] == "measured"
        else [f"**Not yet measured** ({s['clips']} clips). {s.get('note', '')}"]
    )
    L += [f"- {e['file']}: {e['error']}" for e in s["errors"]]
    L += [
        "",
        "## Latency and cost (live calls in this database)",
        "",
        f"Providers: {live['providers']}.",
        f"- Reply latency: {live['latency']}"
        + (
            " (mock providers: measures the app's own overhead, not real speech services)"
            if live["mock_only"]
            else ""
        ),
        f"- Cost: {live['cost']['status']}; calls measured {live['cost']['calls_measured']}; missing prices {live['cost']['missing_prices']}",
    ]
    return "\n".join(L) + "\n"


def main() -> None:
    results = run_all()
    (EVAL_DIR / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (EVAL_DIR / "results.md").write_text(to_markdown(results), encoding="utf-8")
    for g in results["targets"]:
        print(f"{g['name']}: {g['value']} (target {g['target']}) -> {_mark(g['met'])}")
    print(f"Triage: {results['triage']['summary']} | wrote eval/results.md")


if __name__ == "__main__":
    main()
