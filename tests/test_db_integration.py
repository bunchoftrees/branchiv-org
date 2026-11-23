"""Integration tests against a real Postgres (compose.yaml).

Excluded from the default run; run with: uv run pytest -m integration
Requires DATABASE_URL and an applied schema (uv run alembic upgrade head).
"""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from branchiv_org.config import Settings
from branchiv_org.tracker.refusals import RefusalEvent

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    url = Settings().database_url
    if url is None:
        pytest.skip("DATABASE_URL not set")
    eng = create_engine(url.get_secret_value())
    try:
        with eng.connect():
            pass
    except OperationalError:
        pytest.skip("database not reachable")
    yield eng
    eng.dispose()


def test_vector_extension_installed(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")) == 1


def test_refusal_event_roundtrip(engine: Engine) -> None:
    session_id = f"test-{uuid.uuid4()}"
    with Session(engine) as session:
        session.add(
            RefusalEvent(
                user_id="u1",
                session_id=session_id,
                turn_index=1,
                user_input="integration test input",
                refusal_category="cyber",
                model="claude-opus-5-5",
            )
        )
        session.commit()
        row = session.scalars(
            select(RefusalEvent).where(RefusalEvent.session_id == session_id)
        ).one()
        assert row.reviewed is False
        assert row.created_at.tzinfo is not None
        session.delete(row)
        session.commit()
