"""SQLAlchemy engine / session plumbing (SQLite by default, file based)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import Settings, get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # pragma: no cover - driver hook
    """Enable FK enforcement + sane journaling on SQLite connections."""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
    except Exception:
        pass


def init_engine(settings: Settings | None = None, url: str | None = None) -> Engine:
    """Create (and cache) the engine.  Pass ``url`` to override, e.g. in tests."""
    global _engine, _SessionFactory
    settings = settings or get_settings()
    db_url = url or settings.sqlalchemy_url
    if _engine is None or str(_engine.url) != db_url:
        if db_url.startswith("sqlite"):
            path = db_url.replace("sqlite:///", "")
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(
                db_url,
                echo=settings.debug,
                future=True,
                connect_args={"check_same_thread": False, "timeout": 30},
            )
        else:  # pragma: no cover - non sqlite backends are supported but not the default
            _engine = create_engine(db_url, echo=settings.debug, future=True, pool_pre_ping=True)
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    return _engine or init_engine()


def get_session_factory() -> sessionmaker[Session]:
    if _SessionFactory is None:
        init_engine()
    assert _SessionFactory is not None
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope used by background workers (sender, IMAP sync)."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def create_all() -> None:
    from . import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(get_engine())


def reset_engine() -> None:
    """Drop the cached engine (used by tests that swap databases)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
