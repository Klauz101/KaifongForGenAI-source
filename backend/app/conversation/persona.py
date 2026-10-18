"""System prompts and scripted lines (SPEC 7.2). All wording lives in resources/persona.yaml."""

from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml

RES = Path(__file__).resolve().parents[1] / "resources"


@lru_cache
def lines() -> dict:
    return yaml.safe_load((RES / "persona.yaml").read_text(encoding="utf-8"))


def lang_key(language: str) -> str:
    return "yue" if language == "yue" else "en"


def pair(item: dict, **kw: str) -> dict[str, str]:
    """A {yue, en} line with placeholders filled in."""
    return {k: item[k].format(**kw) for k in ("yue", "en")}


def period(hk_now: datetime) -> str:
    return "morning" if hk_now.hour < 12 else "afternoon" if hk_now.hour < 18 else "evening"


def greeting(
    name_yue: str, name_en: str, ngo: str, hk_now: datetime, fact: dict | None
) -> dict[str, str]:
    """AI-disclosing greeting, with one remembered fact when we have one."""
    g, p = lines()["greeting"], period(hk_now)
    out = {}
    for k, name in (("yue", name_yue), ("en", name_en)):
        parts = [g["hello"][p][k].format(name=name), g["intro"][k].format(ngo=ngo)]
        if fact:
            parts.append(g["memory"][k].format(fact=fact[k]))
        parts.append(g["question"]["morning" if p == "morning" else "other"][k])
        out[k] = ("" if k == "yue" else " ").join(parts)
    return out


def close(time: str) -> dict[str, str]:
    return pair(lines()["close"], time=time)


def system_prompt(
    language: str,
    name: str,
    ngo: str,
    facts: list[dict],
    stage: str,
    interests: list[str],
    topic_desc: str = "",
    nudge_idea: str = "",
) -> str:
    k, ln = lang_key(language), lines()
    parts = [ln["system"][k].format(name=name, ngo=ngo)]
    if facts:
        joined = "; ".join(f[k] for f in facts)
        parts.append(ln["memory"][k].format(name=name, facts=joined))
    stage_tpl = ln["stage"].get(stage)
    if stage_tpl:
        parts.append(
            stage_tpl[k].format(
                interests=", ".join(interests) or "everyday life", topic=topic_desc, idea=nudge_idea
            )
        )
    return "\n".join(parts)
