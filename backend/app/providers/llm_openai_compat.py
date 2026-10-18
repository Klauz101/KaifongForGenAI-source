"""One LLM class for every OpenAI-compatible endpoint (Azure OpenAI, DashScope/Qwen, Hunyuan, DeepSeek...).

The endpoint is configuration, never code (see .env.example, marked `# verify`).
"""

from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

from app.providers.base import LLMProvider, LLMResult, ProviderConfigError, Translator
from app.usage import report_tokens


class OpenAICompatibleLLM(LLMProvider):
    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not (base_url and api_key and model):
            raise ProviderConfigError("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL_CHAT are required.")
        self.model = model
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, http_client=http_client)

    @staticmethod
    def _messages(system: str, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        return [{"role": "system", "content": system}, *messages]

    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.6,
        json_mode: bool = False,
        hint: dict | None = None,
    ) -> LLMResult:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, messages),
            temperature=temperature,
            **kwargs,
        )
        usage = resp.usage
        report_tokens(usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0)
        return LLMResult(
            text=resp.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    async def stream(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.6,
        hint: dict | None = None,
    ) -> AsyncIterator[str]:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, messages),
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
        )
        async for chunk in resp:
            if getattr(chunk, "usage", None):
                report_tokens(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class LLMTranslator(Translator):
    """Translation through any LLMProvider (temperature 0)."""

    name = "llm"

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def to_english(self, text: str, source_language: str) -> str:
        if source_language == "en" or not text.strip():
            return text
        system = (
            "Translate the user's Cantonese (Traditional Chinese) text into natural English. "
            "Output only the translation."
        )
        result = await self._llm.complete(
            system, [{"role": "user", "content": text}], temperature=0.0
        )
        return result.text.strip()
