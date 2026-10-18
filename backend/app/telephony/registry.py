from app.config import get_settings
from app.providers.base import ProviderConfigError
from app.telephony.base import MockTelephony, TelephonyProvider

_instance: TelephonyProvider | None = None


def get_telephony() -> TelephonyProvider:
    global _instance
    if _instance is None:
        s = get_settings()
        if s.telephony_provider == "mock":
            _instance = MockTelephony()
        elif s.telephony_provider == "twilio":
            from app.telephony.twilio_provider import TwilioTelephony

            _instance = TwilioTelephony(
                s.twilio_account_sid, s.twilio_auth_token, s.twilio_from_number
            )
        else:
            raise ProviderConfigError(f"Unknown TELEPHONY_PROVIDER '{s.telephony_provider}'")
    return _instance


def set_telephony(p: TelephonyProvider | None) -> None:
    global _instance
    _instance = p
