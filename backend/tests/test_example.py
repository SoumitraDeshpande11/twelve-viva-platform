"""Example test — a copy-me template for new backend tests.

Shows the two common shapes:
  1. A pure-function unit test (no fixtures, no DB).
  2. An HTTP test using the `client` fixture (fresh isolated SQLite per test,
     local AI provider — see backend/conftest.py).

Run just this file:
    pytest tests/test_example.py -q
"""
from __future__ import annotations

from datetime import timezone

import pytest
from fastapi import HTTPException

from app import main


# --- 1. pure helper ---------------------------------------------------------

def test_normalize_window_bound_assumes_utc_for_naive_input():
    # A timestamp with no offset is treated as UTC.
    parsed = main.normalize_window_bound("2026-06-16T09:00:00", "starts_at")
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.hour == 9


def test_normalize_window_bound_empty_is_none():
    # Blank / whitespace means "no bound".
    assert main.normalize_window_bound("", "starts_at") is None
    assert main.normalize_window_bound("   ", "ends_at") is None


def test_normalize_window_bound_rejects_garbage():
    # Malformed input is a 400, not a silent pass.
    with pytest.raises(HTTPException) as exc:
        main.normalize_window_bound("not-a-date", "starts_at")
    assert exc.value.status_code == 400


# --- 2. HTTP via the client fixture ----------------------------------------

def test_health_reports_local_provider(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["ai_provider"] == "local"  # forced by conftest for tests
