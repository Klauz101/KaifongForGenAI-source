"""Telephony adapter (SPEC 11): the conversation engine never knows which carrier is used."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.providers.base import ProviderConfigError


def normalise_number(raw: str) -> str:
    """'9123 4567' -> '+85291234567'; E.164 numbers pass through."""
    digits = "".join(c for c in raw if c.isdigit() or c == "+")
    if digits.startswith("+"):
        return digits
    return f"+852{digits}" if len(digits) == 8 else f"+{digits}"


class TelephonyProvider(ABC):
    name: str

    @abstractmethod
    async def place_call(self, to: str, voice_url: str, status_url: str) -> str:
        """Start an outbound call; returns the carrier's call id. The carrier then fetches `voice_url`."""

    @abstractmethod
    def valid_signature(self, url: str, params: dict[str, str], signature: str) -> bool:
        """Is this webhook really from the carrier?"""


@dataclass
class MockTelephony(TelephonyProvider):
    """Records calls instead of placing them. A webhook is valid when its signature is 'mock'."""

    name: str = "mock"
    placed: list[dict] = field(default_factory=list)

    async def place_call(self, to: str, voice_url: str, status_url: str) -> str:
        sid = f"CAmock{len(self.placed) + 1:04d}"
        self.placed.append({"sid": sid, "to": to, "voice_url": voice_url, "status_url": status_url})
        return sid

    def valid_signature(self, url: str, params: dict[str, str], signature: str) -> bool:
        return signature == "mock"


def allowed_numbers(raw: str) -> set[str]:
    return {normalise_number(n) for n in raw.split(",") if n.strip()}


__all__ = [
    "MockTelephony",
    "ProviderConfigError",
    "TelephonyProvider",
    "allowed_numbers",
    "normalise_number",
]
