"""Cost estimates per call and per person per month (SPEC 9.3).

Prices come from config/pricing.yaml. A missing price makes the cost "not yet measured" (None): the
estimate is never silently zero and never invented.
"""

from functools import lru_cache
from pathlib import Path

import yaml

PRICING_PATH = Path(__file__).resolve().parents[2] / "config" / "pricing.yaml"
# usage key -> (price key, divisor): cost = usage / divisor * price
COMPONENTS = {
    "stt": (("stt_seconds",), "stt_per_minute", 60.0),
    "tts": (("tts_chars",), "tts_per_1k_chars", 1000.0),
    "llm_input": (
        ("llm_chat_in", "llm_extract_in", "llm_translate_in"),
        "llm_input_per_1m_tokens",
        1_000_000.0,
    ),
    "llm_output": (
        ("llm_chat_out", "llm_extract_out", "llm_translate_out"),
        "llm_output_per_1m_tokens",
        1_000_000.0,
    ),
    "telephony": (("telephony_minutes",), "telephony_per_minute", 1.0),
}


@lru_cache
def load_pricing() -> dict:
    return yaml.safe_load(PRICING_PATH.read_text(encoding="utf-8"))


def estimate(usage: dict | None, pricing: dict | None = None) -> dict:
    """{"hkd": float | None, "parts": {component: hkd}, "missing": [price keys still unset]}."""
    pricing = pricing or load_pricing()
    usage = usage or {}
    parts: dict[str, float] = {}
    missing: list[str] = []
    for name, (keys, price_key, divisor) in COMPONENTS.items():
        used = sum(usage.get(k, 0) for k in keys)
        if not used:
            continue
        price = pricing["prices"].get(price_key)
        if price is None:
            missing.append(price_key)
        else:
            parts[name] = round(used / divisor * price, 6)
    return {
        "hkd": None if missing else round(sum(parts.values()), 6),
        "parts": parts,
        "missing": sorted(missing),
    }


def summarise(usages: list[dict], pricing: dict | None = None) -> dict:
    """Average cost per call and the monthly estimate per person, over calls that could be priced."""
    pricing = pricing or load_pricing()
    results = [estimate(u, pricing) for u in usages]
    priced = [r["hkd"] for r in results if r["hkd"] is not None]
    missing = sorted({m for r in results for m in r["missing"]})
    avg = sum(priced) / len(priced) if priced else None
    return {
        "currency": pricing.get("currency", "HKD"),
        "calls_measured": len(usages),
        "calls_priced": len(priced),
        "avg_cost_per_call": None if avg is None else round(avg, 4),
        "est_monthly_per_person": (
            None if avg is None else round(avg * pricing["calls_per_month"], 2)
        ),
        "calls_per_month": pricing["calls_per_month"],
        "missing_prices": missing,
        "status": "ok" if priced else "not yet measured",
    }
