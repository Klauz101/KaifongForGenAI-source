"""Write eval/deck_numbers.md: every value the pitch deck has a bracket for (SPEC Section 19).

Rules: never print a number that was not measured; a missing value says "not yet measured"; anything measured on
synthetic data is marked "(synthetic)"; "users tested" is entered by hand in manual_numbers.yaml and never auto-filled.
Run: `make deck-numbers`.
"""

from datetime import UTC, datetime

from app.config import get_settings
from eval.common import EVAL_DIR, SYNTHETIC_NOTE, load_yaml
from eval.report import run_all

NM = "not yet measured"


def rows(r: dict) -> list[tuple[str, str, str, str, str]]:
    flags, x, t, stt, live = r["redflags"], r["extraction"], r["triage"], r["stt"], r["live"]
    s = get_settings()
    manual = load_yaml(EVAL_DIR / "manual_numbers.yaml")
    med = live["latency"]["median_ms"]
    users = manual.get("users_tested")
    cer = (
        f"{stt['cer_pct']}% over {stt['clips']} clips (provider: {stt['provider']})"
        if stt["status"] == "measured"
        else NM
    )
    llm_name = (
        (s.llm_model_chat or "(not set)")
        if s.llm_provider != "mock"
        else "none: mock mode (no LLM in use)"
    )
    latency = (
        NM
        if med is None
        else f"{med} ms median over {live['latency']['count']} replies"
        + (
            " (mock providers: app overhead only, not real speech services)"
            if live["mock_only"]
            else ""
        )
    )
    c = live["cost"]
    return [
        (
            "8",
            "Voice call status",
            "Browser phone simulator works by typing; spoken and real-phone calls: " + NM,
            "Manual status. No spoken call or real phone call has been verified yet.",
            "n/a",
        ),
        (
            "8, 12",
            "ASR error rate on older voices",
            cer,
            "Character error rate (eval_stt.py) on consented clips",
            "consented real clips" if stt["status"] == "measured" else "no clips yet",
        ),
        ("8, 10", "LLM model name", llm_name, "Configuration (LLM_MODEL_CHAT)", "n/a"),
        (
            "8, 10",
            "Median reply latency",
            latency,
            "Time from input received to first reply audio, per-turn logs of live calls",
            "live calls in this database",
        ),
        (
            "8",
            "Triage rules x/10 correct",
            f"{t['summary']} (synthetic)"
            + ("" if t["agreed_by_team"] else "; expected levels NOT yet agreed by the team"),
            "eval_triage.py on 10 synthetic histories",
            "synthetic",
        ),
        (
            "8",
            "Red flags x/y",
            (
                f"core {sum(1 for r_ in flags['rows'] if r_['group'] == 'core' and r_['alerted'])}/{flags['counts']['core']} caught (synthetic); "
                f"unlisted phrasings {sum(1 for r_ in flags['rows'] if r_['group'] == 'stretch' and r_['alerted'])}/{flags['counts']['stretch']} (synthetic)"
            ),
            "eval_redflags.py",
            "synthetic, developer-written",
        ),
        (
            "8",
            "Extraction agreement",
            f"{x['agreement_pct']}% on {x['cases']} transcripts (synthetic; extractor: {x['provider']}/{x['model']})",
            "eval_extraction.py",
            "synthetic",
        ),
        (
            "8",
            "Dashboard status / URL",
            "Runs locally with synthetic mock data; no deployment",
            "Manual status",
            "n/a",
        ),
        (
            "8, 12",
            "Users tested",
            (
                f"{users} (entered manually: {manual.get('users_tested_note') or 'no note'})"
                if users is not None
                else NM
            ),
            "Manual entry in eval/manual_numbers.yaml; never auto-filled",
            "manual",
        ),
        (
            "10, 13",
            "Cost per call",
            (
                f"{c['currency']} {c['avg_cost_per_call']}"
                if c["avg_cost_per_call"] is not None
                else NM
                + (
                    f" (fill prices: {', '.join(c['missing_prices'])})"
                    if c["missing_prices"]
                    else ""
                )
            ),
            "Metered usage x config/pricing.yaml",
            "live calls in this database",
        ),
        (
            "10, 13",
            "Cost per elder per month",
            (
                f"{c['currency']} {c['est_monthly_per_person']} (cost per call x {c['calls_per_month']})"
                if c["est_monthly_per_person"] is not None
                else NM
            ),
            "Cost per call x calls per month",
            "live calls in this database",
        ),
    ]


def to_markdown(r: dict) -> str:
    lines = [
        "# Numbers for the pitch deck",
        "",
        f"_Generated {datetime.now(UTC).isoformat(timespec='seconds')} by `make deck-numbers`._",
        "",
        f"> {SYNTHETIC_NOTE}",
        "",
        "Rule: a missing value says **not yet measured**. Do not replace it with a guess.",
        "",
        "| Slide | Placeholder | Value | How it was measured | On what data |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows(r)]
    return "\n".join(lines) + "\n"


def main() -> None:
    text = to_markdown(run_all())
    (EVAL_DIR / "deck_numbers.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
