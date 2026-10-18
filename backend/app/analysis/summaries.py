"""Plain-language summaries (EN + 粵) composed from an extraction. Used offline and as a safe fallback."""

from functools import lru_cache
from pathlib import Path

import yaml

RES = Path(__file__).resolve().parents[1] / "resources"
LOC_YUE = {
    "knee": "膝頭哥",
    "back": "背脊",
    "leg": "腳",
    "head": "頭",
    "shoulder": "膊頭",
    "chest": "心口",
}
NO_DATA = {
    "en": "Not enough information could be extracted from this call. Please review the transcript.",
    "yue": "呢次通話未能擷取到足夠資料，請查看對話紀錄。",
}


@lru_cache
def _snippets() -> dict:
    return yaml.safe_load((RES / "summary_snippets.yaml").read_text(encoding="utf-8"))


def compose(x: dict) -> tuple[str, str]:
    """Summary sentences for the flags present in extraction dict `x`."""
    sn, flags = _snippets(), []
    val = lambda k: x[k]["value"]
    if x["overall_confidence"] == "low" and val("mood") is None and val("appetite") == "unknown":
        return NO_DATA["en"], NO_DATA["yue"]
    if val("appetite") == "skipped_meals":
        flags.append("skipped_meals")
    if val("went_out_today") == "no":
        flags.append("homebound")
    if val("pain") in ("moderate", "severe"):
        flags.append("pain")
    if val("sleep") == "poor":
        flags.append("poor_sleep")
    if val("loneliness_expressed"):
        flags.append("lonely")
    if val("confusion_cues") != ["none"]:
        flags.append("confusion")
    if isinstance(val("mood"), int) and val("mood") <= 2:
        flags.append("low_mood")
    if val("meds_taken") in ("no", "partial"):
        flags.append("meds_missed")
    flags = flags or ["stable"]
    loc = x["pain"].get("location") or "body"
    return (
        " ".join(sn[f]["en"].format(loc=loc) for f in flags),
        "".join(sn[f]["yue"].format(loc_yue=LOC_YUE.get(loc, loc)) for f in flags),
    )
