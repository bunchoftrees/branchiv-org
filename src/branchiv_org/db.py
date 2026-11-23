"""Shared SQLAlchemy base plus a lazily created engine and session factory."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from branchiv_org.config import get_settings


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    url = get_settings().database_url
    if url is None:
        raise RuntimeError("DATABASE_URL is not set")
    return url.get_secret_value()


@lru_cache
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Yield a session and always close it; usable as a FastAPI dependency."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
