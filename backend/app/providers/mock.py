"""Offline mock providers so the whole app runs with zero API keys."""

import json
import re
from functools import lru_cache
from pathlib import Path

import yaml

from app.providers.base import (
    LLMProvider,
    LLMResult,
    STTNotConfiguredError,
    STTProvider,
    STTResult,
    Translator,
    TTSProvider,
    TTSResult,
)

RES = Path(__file__).resolve().parents[1] / "resources"


@lru_cache
def _res(name: str) -> dict:
    return yaml.safe_load((RES / name).read_text(encoding="utf-8"))


class MockSTT(STTProvider):
    """Treats the 'audio' bytes as UTF-8 text so the audio path can be tested without speech models."""

    name = "mock"
    needs_wav = False

    async def transcribe(self, audio: bytes, language: str) -> STTResult:
        try:
            text = audio.decode("utf-8").strip()
        except UnicodeDecodeError:
            # Real audio: the mock cannot transcribe it. Say so loudly instead of "hearing" silence.
            raise STTNotConfiguredError(
                "Server speech recognition is not set up (STT_PROVIDER=mock), so spoken audio cannot be "
                "transcribed. Use the browser's speech recognition, type your reply (Chinese or Jyutping), "
                "or set STT_PROVIDER=azure or tencent in .env."
            ) from None
        return STTResult(text=text, confidence=0.9 if text else 0.0)


class MockTTS(TTSProvider):
    name = "mock"

    async def synthesize(self, text: str, language: str) -> TTSResult:
        return TTSResult(audio=None)  # the browser falls back to speechSynthesis


class MockLLM(LLMProvider):
    """Template replies driven by `hint` (stage, topic, memory...) so a typed call feels real."""

    name = "mock"
    streams = False

    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.6,
        json_mode: bool = False,
        hint: dict | None = None,
    ) -> LLMResult:
        hint = hint or {}
        if json_mode:
            if hint.get("task") == "extract":  # offline rule-based extraction (mock mode only)
                from app.analysis.mock_extractor import extract

                return LLMResult(text=json.dumps(extract(hint["turns"], hint.get("nudge_turn"))))
            return LLMResult(text="{}")
        tpl, lang = _res("mock_llm.yaml"), hint.get("language", "yue")
        n = hint.get("elder_turns", 0)
        ack = tpl["acks"][n % len(tpl["acks"])]
        stage = hint.get("stage", "chat")
        parts = {"yue": ack["yue"], "en": ack["en"]}

        def add(item: dict) -> None:
            parts["yue"] += item["yue"]
            parts["en"] += " " + item["en"]

        if stage == "nudge":
            add(hint["nudge_idea"])
        elif stage == "checkin" and hint.get("topic_q"):
            add(hint["topic_q"])
        else:
            facts = hint.get("facts") or []
            if facts and n == 2:
                ref = tpl["memory_ref"]
                add(
                    {
                        "yue": ref["yue"].format(fact=facts[0]["yue"]),
                        "en": ref["en"].format(fact=facts[0]["en"]),
                    }
                )
            else:
                interests = hint.get("interests") or ["tea"]
                q = tpl["chat_questions"][(n // 2) % len(tpl["chat_questions"])]
                interest = interests[n % len(interests)]
                shown = {"yue": tpl["interests_yue"].get(interest, interest), "en": interest}
                add({k: q[k].format(interest=shown[k]) for k in ("yue", "en")})
        return LLMResult(
            text=parts[lang] if lang in parts else parts["en"], meta={"text_en": parts["en"]}
        )


_PAIRS: dict[str, str] | None = None


def _known_pairs() -> dict[str, str]:
    """yue -> en for every fixed line in the resource files (unformatted lines only)."""
    global _PAIRS
    if _PAIRS is None:
        pairs: dict[str, str] = {}

        def walk(node: object) -> None:
            if isinstance(node, dict):
                if {"yue", "en"} <= set(node) and all(
                    isinstance(v, str) for v in (node["yue"], node["en"])
                ):
                    pairs[node["yue"]] = node["en"]
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        for name in ("persona.yaml", "checklist.yaml", "mock_llm.yaml"):
            walk(_res(name))
        _PAIRS = pairs
    return _PAIRS


class MockTranslator(Translator):
    name = "mock"

    async def to_english(self, text: str, source_language: str) -> str:
        if source_language == "en":
            return text
        known = _known_pairs().get(text)
        collapsed = re.sub(r"\s+", " ", text)
        return known or f"[EN] {collapsed}"
