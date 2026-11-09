import os

# Keep the app's own engine in memory too, so tests never create a leave.db file.
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import Base, get_session  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    # Each test gets a fresh in-memory database and never calls a real LLM.
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def employee(client):
    response = client.post(
        "/employees",
        json={"full_name": "Ada Lovelace", "email": "ada@example.com", "annual_leave_days": 10},
    )
    assert response.status_code == 201
    return response.json()
