"""Fernet field encryption for sensitive columns (PDPO DPP4)."""

import logging
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parents[3] / "data"


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    try:
        return Fernet(settings.fernet_key.encode())
    except (ValueError, TypeError):
        if settings.app_env != "dev":
            raise RuntimeError("FERNET_KEY is missing or invalid (run `make keys`).") from None
    # Dev only: persist a throwaway key next to the DB so local data stays readable.
    key_file = DATA_DIR / "dev_fernet.key"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not key_file.exists():
        key_file.write_bytes(Fernet.generate_key())
        logger.warning("FERNET_KEY not set; generated a dev-only key at %s", key_file)
    return Fernet(key_file.read_bytes())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


class EncryptedStr(TypeDecorator):
    """SQLAlchemy column type that encrypts on write and decrypts on read."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        return None if value is None else encrypt(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        return None if value is None else decrypt(value)
