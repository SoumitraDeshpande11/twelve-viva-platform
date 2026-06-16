"""Shared pytest setup: force a self-contained test environment before app import."""
from __future__ import annotations

import os

# Must be set before app.main is imported (module-level os.getenv reads).
os.environ["TWELVE_ENV"] = "test"
os.environ["TWELVE_SECRET_KEY"] = "test-secret-key-deterministic"
os.environ["TWELVE_AI_PROVIDER"] = "local"
os.environ["TWELVE_TRANSCRIPTION_PROVIDER"] = "local"
os.environ.pop("TWELVE_BOOTSTRAP_ADMIN_EMAIL", None)
os.environ.pop("TWELVE_BOOTSTRAP_ADMIN_PASSWORD", None)
# Pre-seed empty so load_dotenv(override=False) in app.main won't pull a real
# TWELVE_BOOTSTRAP_TOKEN from the developer's .env into the isolated test env.
os.environ["TWELVE_BOOTSTRAP_TOKEN"] = ""

import pytest
from fastapi.testclient import TestClient

from app import main, storage


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient backed by a fresh SQLite DB and upload dir per test."""
    data = tmp_path / "data"
    uploads = data / "uploads"
    monkeypatch.setattr(storage, "DATA_DIR", data)
    monkeypatch.setattr(storage, "DB_PATH", data / "twelve.db")
    monkeypatch.setattr(storage, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(main, "UPLOAD_DIR", uploads)
    with TestClient(main.app) as test_client:  # context manager runs startup -> init_db
        yield test_client


@pytest.fixture
def fresh_client(client):
    """A second client (separate cookie jar) sharing the same DB — for the student role."""
    return TestClient(main.app)
