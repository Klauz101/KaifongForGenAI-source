"""Azure text-to-speech via the documented REST API (SSML in, MP3 out).

Contract checked against learn.microsoft.com (rest-text-to-speech): regional endpoint
`https://{region}.tts.speech.microsoft.com/cognitiveservices/v1`, headers `Ocp-Apim-Subscription-Key`,
`Content-Type: application/ssml+xml`, `X-Microsoft-OutputFormat`, `User-Agent`. Not yet exercised live.
"""

from xml.sax.saxutils import escape

import httpx

from app.providers.base import ProviderConfigError, TTSProvider, TTSResult

OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
XML_LANG = {"yue": "zh-HK", "en": "en-US"}


def build_ssml(text: str, voice: str, language: str, rate: str = "-10%") -> str:
    """Slightly slower speech for older listeners."""
    lang = XML_LANG.get(language, "zh-HK")
    return (
        f"<speak version='1.0' xml:lang='{lang}'><voice xml:lang='{lang}' name='{voice}'>"
        f"<prosody rate='{rate}'>{escape(text)}</prosody></voice></speak>"
    )


class AzureTTS(TTSProvider):
    name = "azure"

    def __init__(
        self,
        key: str,
        region: str,
        voice_yue: str,
        voice_en: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not key:
            raise ProviderConfigError("AZURE_SPEECH_KEY is required for the Azure TTS provider.")
        self._url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        self._key = key
        self._voices = {"yue": voice_yue, "en": voice_en}
        self._http = http_client or httpx.AsyncClient(timeout=20)

    async def synthesize(self, text: str, language: str) -> TTSResult:
        voice = self._voices.get(language, self._voices["yue"])
        resp = await self._http.post(
            self._url,
            headers={
                "Ocp-Apim-Subscription-Key": self._key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
                "User-Agent": "kaifong-prototype",
            },
            content=build_ssml(text, voice, language).encode("utf-8"),
        )
        resp.raise_for_status()
        return TTSResult(audio=resp.content, mime="audio/mpeg")
