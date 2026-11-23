"""Corpus integration tests against a real Postgres. Run with: uv run pytest -m integration"""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from branchiv_org.config import Settings
from branchiv_org.corpus import SCHEMA_EMBEDDING_DIM, Chunk, Document
from branchiv_org.retrieval.embeddings import FakeEmbedder

pytestmark = pytest.mark.integration

TEXTS = [
    "The river floods every spring after the snow melts.",
    "Quarterly budgets are reviewed by the finance committee.",
    "Volunteers planted oak trees along the riverbank.",
]


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


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    doc_id = f"test-{uuid.uuid4()}"
    fake = FakeEmbedder(dim=SCHEMA_EMBEDDING_DIM)
    with Session(engine) as s:
        s.add(
            Document(
                doc_id=doc_id,
                title="Test doc",
                tier=1,
                source_type="test",
                snapshot_version="test",
            )
        )
        s.flush()
        for i, (text, vector) in enumerate(zip(TEXTS, fake.embed_documents(TEXTS), strict=True)):
            s.add(
                Chunk(
                    doc_id=doc_id,
                    chunk_index=i,
                    text=text,
                    embedding=vector,
                    embedding_model=fake.model_name,
                    snapshot_version="test",
                )
            )
        s.commit()
        s.info["doc_id"] = doc_id
        yield s
        s.delete(s.get_one(Document, doc_id))  # chunks cascade
        s.commit()


def test_cosine_nearest_neighbor(session: Session) -> None:
    query = FakeEmbedder(dim=SCHEMA_EMBEDDING_DIM).embed_query(TEXTS[1])
    nearest = session.scalars(
        select(Chunk)
        .where(Chunk.doc_id == session.info["doc_id"])
        .order_by(Chunk.embedding.cosine_distance(query))
        .limit(1)
    ).one()
    assert nearest.chunk_index == 1


def test_full_text_search(session: Session) -> None:
    tsquery = func.plainto_tsquery("english", "rivers")
    rows = session.scalars(
        select(Chunk.chunk_index)
        .where(Chunk.doc_id == session.info["doc_id"], Chunk.tsv.op("@@")(tsquery))
        .order_by(Chunk.chunk_index)
    ).all()
    # English stemming: "rivers" -> "river", which matches chunk 0. "riverbank" is its
    # own lexeme, so chunk 2 does not match.
    assert rows == [0]
