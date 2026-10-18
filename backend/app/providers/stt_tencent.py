"""Tencent Cloud ASR (one-sentence recognition, Cantonese engine).

SDK surface verified from the installed `tencentcloud-sdk-python-asr` package:
`AsrClient.SentenceRecognition(SentenceRecognitionRequest)` with EngSerViceType (e.g. `16k_yue`),
SourceType=1, VoiceFormat, Data (base64), DataLen. The response (`Result`) has NO confidence score,
so `confidence` is None and the engine's low-confidence re-prompt only triggers on empty text.
Not yet exercised against the live service (no credentials in dev); TODO team to verify.
"""

import asyncio
import base64

from app.providers.base import ProviderConfigError, STTProvider, STTResult


class TencentSTT(STTProvider):
    name = "tencent"

    def __init__(
        self, secret_id: str, secret_key: str, region: str, engine_yue: str, engine_en: str
    ) -> None:
        if not (secret_id and secret_key):
            raise ProviderConfigError("TENCENT_SECRET_ID and TENCENT_SECRET_KEY are required.")
        from tencentcloud.asr.v20190614 import asr_client
        from tencentcloud.common import credential

        self._client = asr_client.AsrClient(credential.Credential(secret_id, secret_key), region)
        self._engines = {"yue": engine_yue, "en": engine_en}

    def _request(self, audio: bytes, language: str):
        from tencentcloud.asr.v20190614 import models

        req = models.SentenceRecognitionRequest()
        req.EngSerViceType = self._engines.get(language, self._engines["yue"])
        req.SourceType = 1
        req.VoiceFormat = "wav"
        req.Data = base64.b64encode(audio).decode()
        req.DataLen = len(audio)
        return req

    async def transcribe(self, audio: bytes, language: str) -> STTResult:
        # The SDK is synchronous; keep the event loop free.
        resp = await asyncio.to_thread(
            self._client.SentenceRecognition, self._request(audio, language)
        )
        return STTResult(text=(resp.Result or "").strip(), confidence=None)
