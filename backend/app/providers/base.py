"""Provider interfaces. Vendors implement these; the app never imports a vendor directly."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


class ProviderConfigError(RuntimeError):
    """A real provider is selected but its configuration is missing or invalid."""


class STTNotConfiguredError(ProviderConfigError):
    """The speech recogniser in use cannot transcribe real audio (e.g. the offline mock)."""


@dataclass
class STTResult:
    text: str
    confidence: float | None = None  # None = provider does not report one


@dataclass
class TTSResult:
    """audio is None when the client should fall back to browser speechSynthesis."""

    audio: bytes | None
    mime: str = "audio/mpeg"


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    meta: dict = field(default_factory=dict)  # e.g. {"text_en": ...} from the mock LLM


class STTProvider(ABC):
    name: str
    needs_wav: bool = True  # engine converts browser audio to 16 kHz mono WAV first

    @abstractmethod
    async def transcribe(self, audio: bytes, language: str) -> STTResult: ...


class TTSProvider(ABC):
    name: str

    @abstractmethod
    async def synthesize(self, text: str, language: str) -> TTSResult: ...


class LLMProvider(ABC):
    name: str
    streams: bool = (
        True  # False: the service uses `complete` (e.g. the mock, which also returns meta)
    )

    @abstractmethod
    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.6,
        json_mode: bool = False,
        hint: dict | None = None,
    ) -> LLMResult:
        """`hint` is structured context used only by the offline mock; real providers ignore it."""

    async def stream(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.6,
        hint: dict | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas. Default: one chunk from `complete`."""
        result = await self.complete(system, messages, temperature=temperature, hint=hint)
        yield result.text


class Translator(ABC):
    name: str

    @abstractmethod
    async def to_english(self, text: str, source_language: str) -> str: ...
