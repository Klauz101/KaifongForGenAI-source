"""Process-wide provider instances (built once from settings)."""

from dataclasses import dataclass

from app.config import get_settings
from app.providers import factory
from app.providers.base import LLMProvider, STTProvider, Translator, TTSProvider


@dataclass
class Providers:
    stt: STTProvider
    tts: TTSProvider
    llm: LLMProvider
    translator: Translator
    llm_extract: LLMProvider | None = (
        None  # separate (e.g. stronger) model for post-call extraction
    )

    @property
    def extractor(self) -> LLMProvider:
        return self.llm_extract or self.llm

    def names(self) -> dict[str, str]:
        return {
            "stt": self.stt.name,
            "tts": self.tts.name,
            "llm": self.llm.name,
            "translate": self.translator.name,
        }


_instance: Providers | None = None


def get_providers() -> Providers:
    global _instance
    if _instance is None:
        s = get_settings()
        _instance = Providers(
            factory.get_stt(s), factory.get_tts(s), factory.get_llm(s), factory.get_translator(s)
        )
    return _instance


def set_providers(p: Providers | None) -> None:
    """Replace (or clear) the shared instance; used by tests."""
    global _instance
    _instance = p
