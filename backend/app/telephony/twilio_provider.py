"""Twilio outbound calls over the documented REST API; webhook signatures via Twilio's own validator.

Contract checked against twilio.com docs: POST /2010-04-01/Accounts/{sid}/Calls.json with To, From, Url,
StatusCallback (+ events); HTTP basic auth (account sid + auth token); response `sid`. Signature
validation deliberately uses the SDK's RequestValidator (Twilio advises against hand-rolling it).
NOT yet exercised against the live service: needs a paid number, a public URL, and the team's say-so.
"""

import httpx
from twilio.request_validator import RequestValidator

from app.telephony.base import ProviderConfigError, TelephonyProvider


class TwilioTelephony(TelephonyProvider):
    name = "twilio"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not (account_sid and auth_token and from_number):
            raise ProviderConfigError(
                "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER are required."
            )
        self._sid, self._token, self._from = account_sid, auth_token, from_number
        self._validator = RequestValidator(auth_token)
        self._http = http_client or httpx.AsyncClient(timeout=20)

    async def place_call(self, to: str, voice_url: str, status_url: str) -> str:
        resp = await self._http.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Calls.json",
            auth=(self._sid, self._token),
            data={
                "To": to,
                "From": self._from,
                "Url": voice_url,
                "StatusCallback": status_url,
                "StatusCallbackEvent": ["completed"],
            },
        )
        resp.raise_for_status()
        return resp.json()["sid"]

    def valid_signature(self, url: str, params: dict[str, str], signature: str) -> bool:
        return bool(signature) and self._validator.validate(url, params, signature)
