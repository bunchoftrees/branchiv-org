from typing import cast
from unittest.mock import MagicMock

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import TooManyRequests
from databricks.sdk.service.serving import (
    EmbeddingsV1ResponseEmbeddingElement,
    QueryEndpointResponse,
)

from branchiv_org.config import Settings
from branchiv_org.corpus import SCHEMA_EMBEDDING_DIM
from branchiv_org.retrieval.embeddings import (
    DatabricksEmbedder,
    EmbeddingDimensionError,
    EmbeddingError,
    FakeEmbedder,
)

DIM = 4


def _response(batch: list[str], dim: int = DIM) -> QueryEndpointResponse:
    # Returned out of order on purpose: the embedder must sort by index.
    items = [
        EmbeddingsV1ResponseEmbeddingElement(index=i, embedding=[float(i)] * dim)
        for i in range(len(batch))
    ]
    return QueryEndpointResponse(data=list(reversed(items)))


def _embedder(sdk: MagicMock, batch_size: int = 2) -> DatabricksEmbedder:
    settings = Settings(embedding_dim=DIM, embedding_batch_size=batch_size)
    return DatabricksEmbedder(settings, client=cast(WorkspaceClient, sdk))


def test_batches_and_preserves_order() -> None:
    sdk = MagicMock()
    sdk.serving_endpoints.query.side_effect = lambda name, input: _response(input)

    vectors = _embedder(sdk).embed_documents(["a", "b", "c", "d", "e"])

    assert len(vectors) == 5
    calls = sdk.serving_endpoints.query.call_args_list
    assert [c.kwargs["input"] for c in calls] == [["a", "b"], ["c", "d"], ["e"]]
    assert all(c.kwargs["name"] == "databricks-gte-large-en" for c in calls)
    # Within each batch, vectors come back in input order.
    assert [v[0] for v in vectors] == [0.0, 1.0, 0.0, 1.0, 0.0]


def test_dimension_mismatch_raises() -> None:
    sdk = MagicMock()
    sdk.serving_endpoints.query.side_effect = lambda name, input: _response(input, dim=DIM + 1)
    with pytest.raises(EmbeddingDimensionError):
        _embedder(sdk).embed_query("q")


def test_missing_embeddings_raise() -> None:
    sdk = MagicMock()
    sdk.serving_endpoints.query.return_value = QueryEndpointResponse(data=[])
    with pytest.raises(EmbeddingError):
        _embedder(sdk).embed_query("q")


def test_sdk_errors_are_wrapped() -> None:
    sdk = MagicMock()
    sdk.serving_endpoints.query.side_effect = TooManyRequests("slow down")
    with pytest.raises(EmbeddingError) as info:
        _embedder(sdk).embed_query("q")
    assert isinstance(info.value.__cause__, TooManyRequests)


def test_constructing_does_not_touch_network() -> None:
    embedder = DatabricksEmbedder(Settings())
    assert embedder.model_name == "databricks-gte-large-en"
    assert embedder.dim == 1024


def test_fake_embedder_is_deterministic_unit_length() -> None:
    fake = FakeEmbedder(dim=16)
    a, b = fake.embed_query("hello"), fake.embed_query("hello")
    assert a == b
    assert fake.embed_query("other") != a
    assert abs(sum(v * v for v in a) - 1.0) < 1e-9


def test_settings_dim_matches_schema() -> None:
    assert Settings().embedding_dim == SCHEMA_EMBEDDING_DIM
