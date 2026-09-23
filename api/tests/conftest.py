"""
Test fixtures.

Settings are read at import time by most modules, so the environment is
populated before anything under ``app`` is imported. SQLite keeps the tests
self-contained; the models only use portable column types.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("GITHUB_APP_ID", "12345")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("BASE_DOMAIN", "preview.test")
os.environ.setdefault("ENCRYPTION_KEY", "kKXlA6N4zFhNlXKQvR1Z9m6yXn0wF3eHgGqzq7a2C4o=")
os.environ.setdefault("AI_DEPLOYMENT_ENABLED", "false")
os.environ.setdefault("GITHUB_OAUTH_CLIENT_ID", "oauth-id")
os.environ.setdefault("GITHUB_OAUTH_CLIENT_SECRET", "oauth-secret")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, get_db  # noqa: E402
import app.models  # noqa: E402,F401
from app.models import APIToken, User  # noqa: E402


@pytest.fixture(autouse=True)
def held_environment_lock(monkeypatch):
    """
    Tasks take a Redis lock, and the lock no longer fails open, so tests that
    run a task hold it by default. Lock behaviour is tested on its own in
    test_concurrent_deploys.py.
    """
    from contextlib import contextmanager

    import app.core.locks as locks
    import app.tasks.environment as env_tasks

    @contextmanager
    def held(environment_id):
        yield locks.HELD

    monkeypatch.setattr(env_tasks, "environment_lock", held)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db_session):
    from app.main import app

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def user(db_session):
    u = User(github_id=1, github_login="octocat", email="octo@example.com")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def raw_token(db_session, user):
    token = APIToken.generate_token()
    db_session.add(
        APIToken(
            user_id=user.id,
            token_hash=APIToken.hash_token(token),
            token_prefix=token[:8],
            name="test",
        )
    )
    db_session.commit()
    return token


@pytest.fixture()
def auth_headers(raw_token):
    return {"Authorization": f"Bearer {raw_token}"}
