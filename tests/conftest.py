"""Shared fixtures: every test runs against a throwaway SQLite database."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-test-"))
os.environ["LEADGEN_STATE_DIR"] = str(_TMP / "state")
os.environ["LEADGEN_DAILY_RECIPIENT_CAP"] = "400"
os.environ["LEADGEN_BUSINESS_NAME"] = "Test Company Ltd"
os.environ["LEADGEN_BUSINESS_MAILING_ADDRESS"] = "12 Test Street, Suite 4, Springfield, ST 00000"

from leadgen.config import get_settings  # noqa: E402
from leadgen.db import create_all, init_engine, reset_engine  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Recreate the schema in a per-test database file."""
    reset_engine()
    db_path = tmp_path / "test.db"
    engine = init_engine(get_settings(), url=f"sqlite:///{db_path.as_posix()}")
    create_all()
    yield engine
    reset_engine()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from leadgen.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
