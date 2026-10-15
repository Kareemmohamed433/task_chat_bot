"""Pytest configuration and test fixtures."""

from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Employee, Task, AuditLog
from app.seed import seed_employees

# Fixed reference datetime required by assignment specification:
# Tuesday, 2026-10-13 09:00:00 Asia/Riyadh
FIXED_REF_DATETIME = datetime(2026, 10, 13, 9, 0, 0, tzinfo=ZoneInfo("Asia/Riyadh"))


@pytest.fixture(autouse=True)
def configure_test_settings(monkeypatch):
    """Ensure deterministic mode, Riyadh timezone, and reference datetime for all tests."""
    monkeypatch.setattr(settings, "intent_mode", "deterministic")
    monkeypatch.setattr(settings, "timezone", "Asia/Riyadh")
    monkeypatch.setattr(settings, "reference_datetime_override", "2026-10-13 09:00:00")


@pytest.fixture
def db_engine():
    """Create an isolated in-memory SQLite engine with foreign keys enabled."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(db_engine):
    """Yield a fresh database session seeded with baseline employees."""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = TestingSessionLocal()
    seed_employees(session)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db_session):
    """Provide a FastAPI TestClient with the isolated test database dependency."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
