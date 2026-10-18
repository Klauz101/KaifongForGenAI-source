"""Fixed scripted safety replies. Hotline numbers come from settings, never from the YAML (SPEC 8.2)."""

from functools import lru_cache
from pathlib import Path

import yaml

from app.config import get_settings

YAML_PATH = Path(__file__).with_name("responses.yaml")


@lru_cache
def _raw() -> dict:
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))


def _fill(item: dict, name_yue: str, name_en: str) -> dict[str, str]:
    s = get_settings()
    hot = {
        "emergency": s.hotline_emergency,
        "mental_health": s.hotline_mental_health,
        "sbhk": s.hotline_sbhk,
    }
    return {
        "yue": item["yue"].format(name=name_yue, **hot),
        "en": item["en"].format(name=name_en, **hot),
    }


def for_category(category: str, name_yue: str, name_en: str) -> dict[str, str]:
    raw = _raw()
    return _fill(raw.get(category, raw["general"]), name_yue, name_en)


def followup(n: int) -> dict[str, str]:
    items = _raw()["followups"]
    item = items[min(n, len(items)) - 1]
    return {"yue": item["yue"], "en": item["en"]}


def safety_close(name_yue: str, name_en: str) -> dict[str, str]:
    return _fill(_raw()["safety_close"], name_yue, name_en)


def opt_out(kind: str, name_yue: str, name_en: str) -> dict[str, str]:
    return _fill(_raw()["opt_out"][kind], name_yue, name_en)
