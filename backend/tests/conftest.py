import os
import tempfile

from cryptography.fernet import Fernet

# Must be set before the app is imported: isolated DB and a throwaway encryption key.
_tmp = tempfile.mkdtemp(prefix="kaifong-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.main import app
from app.seed.seed import reset_and_seed


@pytest.fixture(scope="session", autouse=True)
def seeded() -> None:
    reset_and_seed()


@pytest.fixture
def db() -> Session:
    with Session(engine) as session:
        yield session


@pytest.fixture
def login():
    """Signed-in clients. Each client keeps one event loop for all its requests (needed for calls)."""
    clients: list[TestClient] = []

    def _login(email: str) -> TestClient:
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        res = client.post("/api/auth/login", json={"email": email, "password": "demo1234"})
        assert res.status_code == 200, res.text
        return client

    yield _login
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture(scope="module")
def reseed_after():
    """Modules that create calls (and so change risk/memory) restore the pristine seed afterwards."""
    yield
    reset_and_seed()


@pytest.fixture
def clean_db():
    """A pristine seed for tests that delete or withdraw things."""
    reset_and_seed()
