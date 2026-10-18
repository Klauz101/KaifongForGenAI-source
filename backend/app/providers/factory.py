"""Pick provider implementations from settings."""

from app.config import Settings, get_settings
from app.providers.base import LLMProvider, STTProvider, Translator, TTSProvider
from app.providers.mock import MockLLM, MockSTT, MockTranslator, MockTTS


def _unsupported(kind: str, name: str) -> NotImplementedError:
    return NotImplementedError(
        f"{kind} provider '{name}' is not available; use mock, azure, tencent or openai_compat."
    )


def get_stt(s: Settings | None = None) -> STTProvider:
    s = s or get_settings()
    if s.stt_provider == "mock":
        return MockSTT()
    if s.stt_provider == "azure":
        from app.providers.stt_azure import AzureSTT

        return AzureSTT(s.azure_speech_key, s.azure_speech_region, s.azure_speech_endpoint)
    if s.stt_provider == "tencent":
        from app.providers.stt_tencent import TencentSTT

        return TencentSTT(
            s.tencent_secret_id,
            s.tencent_secret_key,
            s.tencent_region,
            s.tencent_asr_engine,
            s.tencent_asr_engine_en,
        )
    if s.stt_provider == "google":
        from app.providers.stt_google import GoogleSTT

        return GoogleSTT()
    raise _unsupported("STT", s.stt_provider)


def get_tts(s: Settings | None = None) -> TTSProvider:
    s = s or get_settings()
    if s.tts_provider == "mock":
        return MockTTS()
    if s.tts_provider == "azure":
        from app.providers.tts_azure import AzureTTS

        return AzureTTS(
            s.azure_speech_key, s.azure_speech_region, s.azure_tts_voice, s.azure_tts_voice_en
        )
    if s.tts_provider == "tencent":
        from app.providers.tts_tencent import TencentTTS

        return TencentTTS(
            s.tencent_secret_id,
            s.tencent_secret_key,
            s.tencent_region,
            s.tencent_tts_voice_type,
            s.tencent_tts_voice_type_en,
        )
    raise _unsupported("TTS", s.tts_provider)


def get_llm(s: Settings | None = None) -> LLMProvider:
    s = s or get_settings()
    if s.llm_provider == "mock":
        return MockLLM()
    if s.llm_provider == "openai_compat":
        from app.providers.llm_openai_compat import OpenAICompatibleLLM

        return OpenAICompatibleLLM(s.llm_base_url, s.llm_api_key, s.llm_model_chat)
    raise _unsupported("LLM", s.llm_provider)


def get_translator(s: Settings | None = None) -> Translator:
    s = s or get_settings()
    if s.translate_provider == "mock":
        return MockTranslator()
    if s.translate_provider in ("openai_compat", "llm"):
        from app.providers.llm_openai_compat import LLMTranslator

        return LLMTranslator(get_llm(s))
    raise _unsupported("Translator", s.translate_provider)


def get_extract_llm(s: Settings | None = None) -> LLMProvider | None:
    """A dedicated extraction model when LLM_MODEL_EXTRACT is set (else the chat LLM is reused)."""
    s = s or get_settings()
    if s.llm_provider == "openai_compat" and s.llm_model_extract:
        from app.providers.llm_openai_compat import OpenAICompatibleLLM

        return OpenAICompatibleLLM(s.llm_base_url, s.llm_api_key, s.llm_model_extract)
    return None
