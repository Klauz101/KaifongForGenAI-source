"""Azure Speech-to-text via the documented short-audio REST API (no heavy SDK).

Contract checked against learn.microsoft.com (rest-speech-to-text-short): POST WAV PCM 16 kHz mono,
`language` + `format=detailed` query, `Ocp-Apim-Subscription-Key` header, NBest[].Confidence.
Not yet exercised against the live service (no key in dev); TODO team to verify with a real key.
"""

import httpx

from app.providers.base import ProviderConfigError, STTProvider, STTResult

LOCALES = {"yue": "zh-HK", "en": "en-US"}


class AzureSTT(STTProvider):
    name = "azure"

    def __init__(
        self,
        key: str,
        region: str,
        endpoint: str = "",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not key:
            raise ProviderConfigError("AZURE_SPEECH_KEY is required for the Azure STT provider.")
        base = (
            endpoint.rstrip("/") + "/stt"
            if endpoint
            else f"https://{region}.stt.speech.microsoft.com"
        )
        # NOTE: the regional host/path above is the long-standing form; docs now prefer the
        # custom-subdomain endpoint (set AZURE_SPEECH_ENDPOINT). verify
        self._url = f"{base}/speech/recognition/conversation/cognitiveservices/v1"
        self._key = key
        self._http = http_client or httpx.AsyncClient(timeout=20)

    async def transcribe(self, audio: bytes, language: str) -> STTResult:
        resp = await self._http.post(
            self._url,
            params={"language": LOCALES.get(language, "zh-HK"), "format": "detailed"},
            headers={
                "Ocp-Apim-Subscription-Key": self._key,
                "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
                "Accept": "application/json",
            },
            content=audio,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("RecognitionStatus") != "Success" or not body.get("NBest"):
            return STTResult(text="", confidence=0.0)
        best = body["NBest"][0]
        return STTResult(
            text=best.get("Display") or best.get("Lexical", ""), confidence=best.get("Confidence")
        )
