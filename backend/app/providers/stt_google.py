"""Google STT (`yue-Hant-HK`). Not implemented: Tencent and Azure are the supported real stacks."""

from app.providers.base import STTProvider, STTResult


class GoogleSTT(STTProvider):
    name = "google"

    def __init__(self) -> None:
        # TODO: implement with google-cloud-speech once the team has credentials; verify the
        # Cantonese locale code (`yue-Hant-HK`) in Google's language-support docs first.
        raise NotImplementedError("GoogleSTT is a stub. Use mock, azure or tencent.")

    async def transcribe(self, audio: bytes, language: str) -> STTResult:
        raise NotImplementedError
