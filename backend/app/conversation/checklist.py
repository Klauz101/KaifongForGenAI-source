"""Wellbeing checklist topics (SPEC 7.1): which have been covered, which to weave in next."""

from functools import lru_cache
from pathlib import Path

import yaml

RES = Path(__file__).resolve().parents[1] / "resources"


@lru_cache
def topics() -> dict:
    return yaml.safe_load((RES / "checklist.yaml").read_text(encoding="utf-8"))


def order() -> list[str]:
    return list(topics())


def detect(text: str, language: str) -> set[str]:
    """Topics the older person volunteered in this utterance (keyword match)."""
    lowered = text.lower()
    found = set()
    for name, spec in topics().items():
        words = spec["keywords"]["yue" if language == "yue" else "en"]
        if any(w.lower() in lowered for w in words):
            found.add(name)
    return found


def next_topic(covered: set[str]) -> str | None:
    return next((t for t in order() if t not in covered), None)
