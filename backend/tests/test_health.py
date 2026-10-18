import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.providers import factory
from app.providers.base import ProviderConfigError


def test_health_reports_mock_providers() -> None:
    res = TestClient(app).get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert set(body["providers"].values()) == {"mock"}


def test_mock_providers_work_offline() -> None:
    s = Settings()
    assert asyncio.run(factory.get_tts(s).synthesize("你好", "yue")).audio is None
    assert asyncio.run(factory.get_llm(s).complete("sys", [], json_mode=True)).text == "{}"
    assert asyncio.run(factory.get_translator(s).to_english("hi", "en")) == "hi"


def test_unknown_provider_is_a_clear_error() -> None:
    with pytest.raises(ProviderConfigError):  # real provider selected without credentials
        factory.get_stt(Settings(stt_provider="tencent"))
    with pytest.raises(NotImplementedError):
        factory.get_stt(Settings(stt_provider="nonsense"))


def test_the_shipped_env_example_loads_cleanly() -> None:
    """`make setup` copies .env.example to .env: a fresh clone must start with it (empty values = unset)."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / ".env.example"
    s = Settings(_env_file=example)
    assert s.tencent_tts_voice_type is None and s.tencent_tts_voice_type_en is None
    assert s.stt_provider == "mock" and s.llm_provider == "mock" and s.telephony_provider == "mock"
    assert s.telephony_allowed_numbers == ""  # nothing may be dialled by default
