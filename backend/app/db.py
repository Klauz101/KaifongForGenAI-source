"""Database engine and session helpers."""

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings
from app.security.crypto import DATA_DIR

DATA_DIR.mkdir(parents=True, exist_ok=True)
_url = get_settings().database_url or f"sqlite:///{DATA_DIR / 'kaifong.db'}"
engine = create_engine(_url, connect_args={"check_same_thread": False})


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
