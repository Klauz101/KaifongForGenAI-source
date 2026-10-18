"""Phase 7: the evaluation harness (red flags, extraction, triage, STT, report, deck numbers)."""

import json
from pathlib import Path

import pytest
import yaml

from app.analysis.mock_extractor import extract
from app.schemas import Extraction
from eval import deck_numbers, eval_extraction, eval_redflags, eval_stt, eval_triage, report
from eval.common import EVAL_DIR

pytestmark = pytest.mark.usefixtures("reseed_after")


# ---------- red flags ------------------------------------------------------------------------------


def test_redflag_case_file_meets_the_spec_shape() -> None:
    cases = yaml.safe_load((EVAL_DIR / "redflag_cases.yaml").read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 40
    assert (
        sum(1 for c in cases if c["group"] in ("negation", "negative")) >= 10
    )  # SPEC: >= 10 negatives/negations
    assert {c["lang"] for c in cases} >= {"yue", "en"}
    assert len({c["id"] for c in cases}) == len(cases) and {c["group"] for c in cases} == {
        "core",
        "negation",
        "negative",
        "stretch",
    }
    from app.safety.redflags import config

    cats = set(config()["categories"])
    assert all(set(c["expected"]) <= cats for c in cases)
    assert {
        cat for c in cases if c["group"] == "core" for cat in c["expected"]
    } == cats  # every category is covered


def test_core_red_flag_recall_is_100_percent_and_nothing_ordinary_alerts() -> None:
    out = eval_redflags.run()
    assert out["core_misses"] == [], out["core_misses"]  # a miss here is a safety failure
    assert out["core_recall_pct"] == 100.0 and out["core_category_accuracy_pct"] == 100.0
    assert (
        out["false_alarms"] == []
        and out["precision_pct"] == 100.0
        and out["negatives_correct_pct"] == 100.0
    )
    assert set(out["core_recall_by_language"]) == {"yue", "en", "zh"}


def test_the_stretch_set_is_reported_separately_and_not_hidden() -> None:
    out = eval_redflags.run()
    assert out["counts"]["stretch"] >= 10 and out["stretch_recall_pct"] is not None
    # The honest finding: wording outside the phrase list mostly slips through the deterministic layer.
    assert out["stretch_recall_pct"] < 50
    assert len(out["stretch_misses"]) >= 5 and all("text" in m for m in out["stretch_misses"])
    stretch_ids = {r["id"] for r in out["rows"] if r["group"] == "stretch"}
    core_ids = {r["id"] for r in out["rows"] if r["group"] == "core"}
    assert not stretch_ids & core_ids  # stretch cases never count toward the gating number


def test_recall_is_measured_not_assumed(monkeypatch) -> None:
    from app.safety import redflags

    monkeypatch.setattr(
        redflags, "detect", lambda text: redflags.Detection()
    )  # a detector that never fires
    monkeypatch.setattr(eval_redflags, "detect", redflags.detect)
    assert (
        eval_redflags.run()["core_recall_pct"] == 0.0
    )  # the harness would catch a broken detector


# ---------- extraction -----------------------------------------------------------------------------


def test_gold_set_is_well_formed() -> None:
    cases = yaml.safe_load(
        (EVAL_DIR / "extraction_gold" / "cases.yaml").read_text(encoding="utf-8")
    )["cases"]
    assert len(cases) == 30 and len({c["id"] for c in cases}) == 30
    for c in cases:
        gold = eval_extraction.gold_for(c)
        Extraction.model_validate(
            {k: {"value": v} for k, v in gold.items()}
        )  # gold uses only allowed values
        assert (
            c["tag"] in ("easy", "hard", "negation") and c["lang"] in ("yue", "en") and c["turns"]
        )
    assert {c["tag"] for c in cases} == {"easy", "hard", "negation"}
    assert (
        sum(c["tag"] == "hard" for c in cases) >= 5
    )  # paraphrases that are not guaranteed to pass


def test_extraction_agreement_is_computed_with_the_production_code_path() -> None:
    out = eval_extraction.run()
    assert out["cases"] == 30 and out["fields_per_case"] == 9 and out["provider"] == "mock"
    assert out["agreement_pct"] >= 85.0, out["disagreements"]  # SPEC target (mock-mode floor)
    assert (
        out["evidence_valid_pct"] == 100.0
    )  # every value-bearing answer cites the person's own turns
    assert out["by_tag_pct"]["easy"] > out["by_tag_pct"]["hard"]  # the hard set is genuinely harder
    assert out["by_tag_pct"]["hard"] < 100.0  # not tuned to a perfect score
    assert all({"case", "field", "gold", "predicted"} <= set(d) for d in out["disagreements"])


def test_agreement_rules() -> None:
    assert eval_extraction.agrees("mood", 3, 4) and not eval_extraction.agrees("mood", 2, 4)
    assert eval_extraction.agrees("mood", None, None) and not eval_extraction.agrees(
        "mood", None, 3
    )
    assert eval_extraction.agrees(
        "social_contact", ["friend", "neighbour"], ["neighbour", "friend"]
    )
    assert not eval_extraction.agrees(
        "sleep", "unknown", "good"
    )  # inventing an answer is a disagreement


def test_quoting_a_question_or_pills_is_not_read_as_eating_and_quite_a_bit_is_not_a_bit() -> None:
    """Regressions found by the first evaluation run."""

    def turns(text: str) -> list[dict]:
        return [
            {"index": 0, "speaker": "kaifong", "text": "你好"},
            {"index": 1, "speaker": "elder", "text": text},
        ]

    assert extract(turns("咦，你頭先問過我食咗飯未？"))["appetite"]["value"] == "unknown"
    assert extract(turns("食咗一半，另一款唔記得"))["appetite"]["value"] == "unknown"
    assert (
        extract(turns("食咗飯，好飽"))["appetite"]["value"] == "normal"
    )  # real answers still work
    assert extract(turns("my knee hurts quite a bit"))["pain"]["value"] == "moderate"
    assert extract(turns("just a bit of pain"))["pain"]["value"] == "mild"


# ---------- triage ---------------------------------------------------------------------------------


def test_triage_cases_have_ten_histories_and_report_x_of_10() -> None:
    out = eval_triage.run()
    assert out["total"] == 10 and out["summary"] == f"{out['correct']}/10 correct"
    assert {r["expected"] for r in out["rows"]} == {"green", "amber", "red"}
    assert all(r["rationale"] for r in out["rows"]) and any(r["debated"] for r in out["rows"])
    assert out["agreed_by_team"] is False  # must stay false until the team actually agrees
    assert out["correct"] == 10, out["disagreements"]


def test_a_wrong_expectation_shows_up_as_a_disagreement() -> None:
    case = {"id": "x", "title": "stable", "expected": "red", "rationale": "deliberately wrong"}
    from app.analysis.risk import evaluate

    assert (
        evaluate(eval_triage.history(case), eval_triage.AS_OF).level == "green" != case["expected"]
    )


# ---------- speech recognition ---------------------------------------------------------------------


def test_cer_arithmetic() -> None:
    assert eval_stt.cer("今日食咗飯", "今日食咗飯") == 0
    assert eval_stt.cer("今日食咗飯", "今日食飯") == pytest.approx(
        1 / 5
    )  # one deletion in five characters
    assert eval_stt.cer("今日 食咗飯。", "今日食咗飯") == 0  # spaces and punctuation are ignored
    assert eval_stt.cer("Hello world", "hello  world!") == 0
    assert eval_stt.edit_distance("abc", "") == 3
    with pytest.raises(ValueError):
        eval_stt.cer("。", "x")


def test_stt_without_clips_says_not_yet_measured(tmp_path) -> None:
    out = eval_stt.run(tmp_path)
    assert out["status"] == "not yet measured" and out["cer_pct"] is None and out["clips"] == 0


def test_stt_only_uses_consented_clips(tmp_path) -> None:
    (tmp_path / "a.txt").write_text(
        "我今日食咗早餐", encoding="utf-8"
    )  # mock STT "transcribes" text files
    (tmp_path / "b.txt").write_text("我落咗街", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "clips": [
                    {
                        "file": "a.txt",
                        "reference": "我今日食咗早餐",
                        "language": "yue",
                        "consented": True,
                    },
                    {
                        "file": "b.txt",
                        "reference": "我落咗街買餸",
                        "language": "yue",
                        "consented": True,
                    },
                    {
                        "file": "c.txt",
                        "reference": "秘密",
                        "language": "yue",
                    },  # no consent flag: must be skipped
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    out = eval_stt.run(Path(tmp_path))
    assert out["status"] == "measured" and out["clips"] == 2 and out["provider"] == "mock"
    assert out["cer_pct"] == pytest.approx(
        100 * 2 / 13, abs=0.1
    )  # 2 missing characters of 13 reference characters
    assert [c["file"] for c in out["per_clip"]] == ["a.txt", "b.txt"]
    assert out["errors"][0]["file"] == "c.txt" and "consented" in out["errors"][0]["error"]


def test_stt_readme_and_gitignore_protect_real_recordings() -> None:
    folder = EVAL_DIR / "stt_eval"
    assert "consent" in (folder / "README.md").read_text(encoding="utf-8").lower()
    ignored = (folder / ".gitignore").read_text(encoding="utf-8")
    assert "*.webm" in ignored and "manifest.yaml" in ignored


# ---------- report, deck numbers, API ---------------------------------------------------------------


def test_report_writes_results_with_targets_and_honest_labels(monkeypatch) -> None:
    r = report.run_all()
    names = {t["name"]: t for t in r["targets"]}
    assert (
        names["Red-flag recall on core phrases"]["met"] is True
        and names["Extraction agreement with gold"]["met"] is True
    )
    latency = names["Median reply latency"]
    assert latency["met"] in (None, True, False)
    if r["live"]["mock_only"]:
        assert latency["met"] is None  # mock-mode latency never counts as meeting the real target
    md = report.to_markdown(r)
    for needle in (
        "SYNTHETIC",
        "Core recall",
        "Stretch recall",
        "Triage rules",
        "NOT yet agreed",
        "Speech recognition",
        "not yet measured",
    ):
        assert needle.lower() in md.lower(), needle
    assert "deterministic list alone will miss" in md


def test_deck_numbers_never_invent_values(tmp_path, monkeypatch) -> None:
    manual = tmp_path / "manual_numbers.yaml"
    manual.write_text("users_tested: null\nusers_tested_note: ''\n", encoding="utf-8")
    monkeypatch.setattr(deck_numbers, "EVAL_DIR", tmp_path)
    r = report.run_all()
    table = {row[1]: row for row in deck_numbers.rows(r)}
    assert "Users tested" in table and table["Users tested"][2] == "not yet measured"
    assert (
        table["Cost per call"][2].startswith("not yet measured")
        and table["Cost per elder per month"][2] == "not yet measured"
    )
    assert table["ASR error rate on older voices"][2] == "not yet measured"
    assert (
        "synthetic" in table["Triage rules x/10 correct"][2]
        and "NOT yet agreed" in table["Triage rules x/10 correct"][2]
    )
    assert (
        "synthetic" in table["Red flags x/y"][2]
        and "unlisted phrasings" in table["Red flags x/y"][2]
    )
    assert table["LLM model name"][2].startswith("none: mock mode")
    # only a human can fill in "users tested", and it is labelled as such
    manual.write_text(
        "users_tested: 6\nusers_tested_note: '3 social workers, 3 volunteers'\n", encoding="utf-8"
    )
    filled = {row[1]: row for row in deck_numbers.rows(r)}["Users tested"][2]
    assert filled.startswith("6 (entered manually") and "volunteers" in filled
    md = deck_numbers.to_markdown(r)
    assert md.count("|") > 50 and "not yet measured" in md


def test_shipped_manual_numbers_file_is_empty() -> None:
    manual = yaml.safe_load((EVAL_DIR / "manual_numbers.yaml").read_text(encoding="utf-8"))
    assert manual["users_tested"] is None  # never pre-filled by the software


def test_eval_endpoint_is_admin_only_and_serves_the_latest_results(login) -> None:
    report.main()  # what `make eval` does
    saved = json.loads((EVAL_DIR / "results.json").read_text(encoding="utf-8"))
    admin = login("admin@demo.kaifong")
    body = admin.get("/api/metrics/eval").json()["results"]
    assert (
        body["generated_at"] == saved["generated_at"]
        and body["triage"]["summary"] == "10/10 correct"
    )
    assert body["redflags"]["core_recall_pct"] == 100.0 and "stretch_recall_pct" in body["redflags"]
    assert login("worker@demo.kaifong").get("/api/metrics/eval").status_code == 403
    assert (EVAL_DIR / "results.md").exists() and (EVAL_DIR / "results.md").read_text(
        encoding="utf-8"
    ).startswith("# Evaluation results")
