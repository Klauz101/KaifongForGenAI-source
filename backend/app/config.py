"""Application settings, read from environment / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        extra="ignore",
        env_ignore_empty=True,  # `KEY=` in .env means "not set"
    )

    app_env: str = "dev"
    secret_key: str = "change-me"
    fernet_key: str = ""
    ngo_name: str = "Sample Elderly Centre"
    default_language: str = "yue"
    timezone: str = "Asia/Hong_Kong"

    stt_provider: str = "mock"
    tts_provider: str = "mock"
    llm_provider: str = "mock"
    translate_provider: str = "mock"

    # OpenAI-compatible LLM (Azure OpenAI / DashScope Qwen / Hunyuan / DeepSeek ...)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model_chat: str = ""
    llm_model_extract: str = ""

    azure_speech_key: str = ""
    azure_speech_region: str = "eastasia"
    azure_speech_endpoint: str = ""  # optional custom-subdomain host for STT, see .env.example
    azure_tts_voice: str = "zh-HK-HiuMaanNeural"
    azure_tts_voice_en: str = "en-US-JennyNeural"
    tencent_secret_id: str = ""
    tencent_secret_key: str = ""
    tencent_region: str = "ap-hongkong"
    tencent_asr_engine: str = "16k_yue"
    tencent_asr_engine_en: str = "16k_en"
    tencent_tts_voice_type: int | None = None  # Cantonese voice id: verify in Tencent docs
    tencent_tts_voice_type_en: int | None = None

    stt_min_confidence: float = 0.55
    tts_cache_size: int = 256
    max_elder_turns: int = 8
    max_call_seconds: int = 300
    retain_audio: bool = False
    audio_retention_hours: int = 24
    data_retention_days: int = 180

    # TODO: team must verify all hotline numbers before the demo.
    hotline_emergency: str = "999"
    hotline_mental_health: str = "18111"
    hotline_sbhk: str = Field(default="2389 2222")

    database_url: str = ""  # default: sqlite file in data/
    # Telephony (SPEC 11). Default mock: no real calls, no spend. Twilio needs a paid number and a public URL.
    telephony_provider: str = "mock"  # mock | twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    public_base_url: str = ""  # e.g. an ngo ngrok URL; Twilio signs webhooks against it
    telephony_allowed_numbers: str = ""  # comma-separated E.164 numbers Kaifong may ever dial
    twilio_speech_language_yue: str = (
        "yue-Hant-HK"  # from Twilio's Gather docs; verify current support
    )
    twilio_speech_language_en: str = "en-US"
    withdrawal_grace_days: int = (
        30  # after consent withdrawal, personal data is purged this many days later
    )
    retention_job_interval_hours: int = 24
    provider_no_training_confirmed: bool = (
        False  # team confirms vendors' "no training on our data" is on
    )
    safety_llm_classifier: bool = False  # optional extra screen; it can only add flags
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
