"""Tencent Cloud TTS (`TextToVoice`).

SDK surface verified from the installed `tencentcloud-sdk-python-tts` package: TextToVoiceRequest has
Text (max ~150 Chinese chars), SessionId, VoiceType (int), Codec (wav|mp3|pcm), SampleRate,
PrimaryLanguage (1 zh, 2 en), Speed; the response `Audio` is base64. The Cantonese VoiceType id must be
taken from Tencent's voice list (TENCENT_TTS_VOICE_TYPE) - deliberately not guessed here.
Not yet exercised against the live service; TODO team to verify.
"""

import asyncio
import base64
import uuid

from app.providers.base import ProviderConfigError, TTSProvider, TTSResult


class TencentTTS(TTSProvider):
    name = "tencent"

    def __init__(
        self,
        secret_id: str,
        secret_key: str,
        region: str,
        voice_yue: int | None,
        voice_en: int | None,
        speed: float = -1,  # -1 = 0.8x, a little slower for older listeners
    ) -> None:
        if not (secret_id and secret_key):
            raise ProviderConfigError("TENCENT_SECRET_ID and TENCENT_SECRET_KEY are required.")
        if voice_yue is None:
            raise ProviderConfigError(
                "TENCENT_TTS_VOICE_TYPE (Cantonese voice id) is required; verify in docs."
            )
        from tencentcloud.common import credential
        from tencentcloud.tts.v20190823 import tts_client

        self._client = tts_client.TtsClient(credential.Credential(secret_id, secret_key), region)
        self._voices = {"yue": voice_yue, "en": voice_en if voice_en is not None else voice_yue}
        self._speed = speed

    def _request(self, text: str, language: str):
        from tencentcloud.tts.v20190823 import models

        req = models.TextToVoiceRequest()
        req.Text = text
        req.SessionId = uuid.uuid4().hex
        req.VoiceType = self._voices.get(language, self._voices["yue"])
        req.Codec = "mp3"
        req.PrimaryLanguage = 2 if language == "en" else 1
        req.Speed = self._speed
        return req

    async def synthesize(self, text: str, language: str) -> TTSResult:
        resp = await asyncio.to_thread(self._client.TextToVoice, self._request(text, language))
        return TTSResult(audio=base64.b64decode(resp.Audio), mime="audio/mpeg")
