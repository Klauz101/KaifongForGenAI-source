"""Deterministic red-flag detector (SPEC 8.1). Pure Python, no network, runs before the LLM.

It errs toward triggering. Negation ("我冇跌親", "I did not fall") suppresses a flag for most categories
and is logged as a near-miss for review; self-harm is never suppressed. An optional LLM classifier may
*add* flags but can never remove one found here.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app import jyutping
from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)
YAML_PATH = Path(__file__).with_name("redflag_phrases.yaml")

try:  # optional: Traditional/Simplified folding for key characters
    import opencc  # type: ignore[import-not-found]

    _S2T = opencc.OpenCC("s2hk")
except Exception:  # noqa: BLE001 - opencc is optional; the YAML already lists Mandarin variants
    _S2T = None

PUNCT = re.compile(
    r"[\s　，。！？、；：,.!?;:'\"`~()（）「」『』【】\[\]{}<>《》…—\-_/\\|*#@$%^&+=]+"
)


@dataclass(frozen=True)
class Hit:
    category: str
    phrase: str


@dataclass
class Detection:
    hits: list[Hit] = field(default_factory=list)  # triggers: the call escalates
    near_misses: list[Hit] = field(default_factory=list)  # negated mentions: logged for review only

    @property
    def categories(self) -> list[str]:
        return list(dict.fromkeys(h.category for h in self.hits))


@lru_cache
def config() -> dict:
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))


def _fold(text: str) -> str:
    text = text.lower().replace("’", "'").replace("'", "")
    return _S2T.convert(text) if _S2T else text


def _cjk(text: str) -> str:
    """Letters/characters only: spaces and punctuation removed (for CJK substring matching)."""
    return PUNCT.sub("", _fold(text))


def _words(text: str) -> list[str]:
    return [w for w in PUNCT.sub(" ", _fold(text)).split(" ") if w]


def _is_cjk(phrase: str) -> bool:
    return any("一" <= c <= "鿿" for c in phrase)


def _cjk_negated(norm: str, start: int) -> bool:
    cfg = config()
    window = cfg["negation_window"]["cjk_chars"]
    before = norm[max(0, start - window) : start]
    return any(n in before for n in cfg["negators"]["cjk"])


def _en_negated(words: list[str], start: int) -> bool:
    cfg = config()
    before = words[max(0, start - cfg["negation_window"]["en_words"]) : start]
    return any(w in cfg["negators"]["en"] for w in before)


def _strip_idioms(text: str) -> str:
    folded = _fold(text)
    for idiom in config().get("en_exceptions", []):
        folded = re.sub(rf"\b{re.escape(idiom)}\b", " ", folded)
    return folded


def _find_phrases(text: str, phrases: list[str]) -> list[tuple[str, bool]]:
    """Every phrase occurring in `text` as (phrase, negated)."""
    norm, words = _cjk(text), _words(_strip_idioms(text))
    found: list[tuple[str, bool]] = []
    for phrase in phrases:
        if _is_cjk(phrase):
            p = _cjk(phrase)
            i = norm.find(p)
            while i != -1:
                found.append((phrase, _cjk_negated(norm, i)))
                i = norm.find(p, i + 1)
        else:
            tokens = _words(phrase)
            for i in range(len(words) - len(tokens) + 1):
                if words[i : i + len(tokens)] == tokens:
                    found.append((phrase, _en_negated(words, i)))
    return found


def detect(text: str) -> Detection:
    """Scan one utterance. Hits trigger an escalation; negated mentions become near-misses.

    Text typed as Jyutping ("ngo5 dit3 zo2") is screened too: tone-insensitively, against the same phrases.
    """
    out, cfg = Detection(), config()
    for name, spec in cfg["categories"].items():
        phrases = [*spec.get("yue", []), *spec.get("zh", []), *spec.get("en", [])]
        suppress = spec.get("suppress_on_negation", True)
        for phrase, negated in _find_phrases(text, phrases):
            hit = Hit(name, phrase)
            bucket = out.near_misses if (negated and suppress) else out.hits
            if hit not in bucket:
                bucket.append(hit)
    merge(
        out, detect_jyutping(text)
    )  # Jyutping (with or without tones), even inside an English sentence
    prio = cfg["priority"]
    out.hits.sort(key=lambda h: prio.index(h.category) if h.category in prio else len(prio))
    return out


def merge(into: Detection, other: Detection) -> Detection:
    """Add `other`'s hits and near-misses to `into` (never removes anything)."""
    for src, dst in ((other.hits, into.hits), (other.near_misses, into.near_misses)):
        for h in src:
            if h not in dst:
                dst.append(h)
    return into


# ---------------------------------------------------------------------------------------------
# Jyutping typed as input: match phrase syllables, ignoring tones (people mistype them)
# ---------------------------------------------------------------------------------------------
JP_NEGATORS = {"mou", "mei", "m"}  # 冇 / 未 / 唔 as the syllable right before a phrase


@lru_cache
def _jyutping_phrases() -> list[tuple[str, str, tuple[str, ...]]]:
    import pycantonese

    out = []
    for name, spec in config()["categories"].items():
        for phrase in spec.get("yue", []):
            segs = pycantonese.characters_to_jyutping(phrase)
            if segs and all(j for _, j in segs):
                out.append(
                    (name, phrase, tuple(jyutping.toneless(s) for _, j in segs for s in j.split()))
                )
    return out


def detect_jyutping(text: str) -> Detection:
    """Red-flag screen for Jyutping input (also used on the raw Jyutping when it was converted to characters)."""
    out, cats = Detection(), config()["categories"]
    # Every Latin word counts as a syllable, tone digits or not ("zou2san4" -> zou, san): people often skip the
    # tones, and an emergency must not slip through because of that. Phrases are matched as whole sequences.
    syl = re.findall(r"[a-z]+", text.lower()[: jyutping.MAX_INPUT_CHARS])
    for name, phrase, pat in _jyutping_phrases():
        n = len(pat)
        for i in range(len(syl) - n + 1):
            if tuple(syl[i : i + n]) != pat:
                continue
            negated = any(s in JP_NEGATORS for s in syl[max(0, i - 2) : i])
            hit = Hit(name, phrase)
            bucket = (
                out.near_misses
                if (negated and cats[name].get("suppress_on_negation", True))
                else out.hits
            )
            if hit not in bucket:
                bucket.append(hit)
    return out


def label(category: str) -> dict[str, str]:
    cats = config()["categories"]
    return (
        cats[category]["label"]
        if category in cats
        else {"en": "a safety concern", "zh": "有安全關注"}
    )


# ---------------------------------------------------------------------------------------------
# Opt-out ("please stop calling me")
# ---------------------------------------------------------------------------------------------


def detect_opt_out(text: str) -> bool:
    o = config()["opt_out"]
    return bool(_find_phrases(text, [*o["yue"], *o["zh"], *o["en"]]))


def declines_opt_out(text: str) -> bool:
    d = config()["declines_optout"]
    return bool(_find_phrases(text, [*d["yue"], *d["en"]]))


# ---------------------------------------------------------------------------------------------
# Optional LLM classifier: can only ADD a flag (e.g. indirect hopelessness), never remove one.
# ---------------------------------------------------------------------------------------------

CLASSIFIER_SYSTEM = (
    "You screen one sentence said by an older person on a friendly phone call. Reply with JSON only: "
    '{"concerning": true|false}. concerning=true only if it suggests hopelessness, thoughts of ending '
    "their life, a fall or being unable to get up, chest pain, trouble breathing, stroke signs, fainting "
    "or other urgent danger, even if said indirectly. Otherwise false."
)


async def classify_with_llm(llm: LLMProvider, text: str) -> Hit | None:
    try:
        result = await llm.complete(
            CLASSIFIER_SYSTEM, [{"role": "user", "content": text}], temperature=0.0, json_mode=True
        )
        data = json.loads(result.text[result.text.find("{") : result.text.rfind("}") + 1])
    except Exception:
        logger.exception("LLM safety classifier failed (ignored)")
        return None
    return Hit("llm_flag", "llm") if data.get("concerning") is True else None
