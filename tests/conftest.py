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
from leadgen.security import reset_vault  # noqa: E402
from leadgen.services.llm import reset_llm  # noqa: E402
from leadgen.services.scrapers.pipeline import reset_pipeline  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Give every test a genuinely private database.

    Pointing ``LEADGEN_STATE_DIR`` at ``tmp_path`` (rather than passing an
    explicit ``url`` to ``init_engine``) matters: ``create_app()`` calls
    ``init_engine(settings)`` with no URL, and ``init_engine`` rebuilds the
    engine whenever the URL differs. Passing ``url=`` here therefore handed the
    first ``TestClient`` a *shared* database while the test believed it had a
    private one, so rows leaked between tests. Aligning the settings URL with
    the per-test path makes both call sites resolve to the same file.
    """
    def _reset_singletons() -> None:
        # These cache a Settings object at construction time, so they must be
        # dropped whenever settings change or a previous test's config leaks in.
        reset_engine()
        reset_pipeline()
        reset_llm()
        reset_vault()
        get_settings.cache_clear()

    _reset_singletons()
    monkeypatch.setenv("LEADGEN_STATE_DIR", str(tmp_path))
    _reset_singletons()
    engine = init_engine(get_settings())
    create_all()
    yield engine
    _reset_singletons()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from leadgen.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
