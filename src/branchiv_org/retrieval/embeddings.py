"""Embedding models: a common protocol, the Databricks implementation, and a fake for tests."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Sequence
from typing import Protocol

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors.base import DatabricksError

from branchiv_org.config import Settings


class EmbeddingError(Exception):
    """An embedding request failed or returned unusable output."""


class EmbeddingDimensionError(EmbeddingError):
    """The model returned vectors of a different length than the schema expects."""


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class DatabricksEmbedder:
    """Embeds text with a Databricks model serving endpoint of task type embeddings.

    The same embedding is used for documents and queries (no query prefix), which
    suits GTE-style models. Revisit if the endpoint changes to a model that expects
    an instruction prefix on queries.
    """

    def __init__(self, settings: Settings, client: WorkspaceClient | None = None) -> None:
        self._endpoint = settings.embedding_endpoint
        self._dim = settings.embedding_dim
        self._batch_size = settings.embedding_batch_size
        self._settings = settings
        self._client = client

    @property
    def model_name(self) -> str:
        return self._endpoint

    @property
    def dim(self) -> int:
        return self._dim

    def _workspace(self) -> WorkspaceClient:
        # Created lazily so constructing the embedder never resolves credentials.
        if self._client is None:
            token = self._settings.databricks_token
            self._client = WorkspaceClient(
                host=self._settings.databricks_host or None,
                token=(token.get_secret_value() or None) if token else None,
            )
        return self._client

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(self._embed_batch(list(texts[start : start + self._batch_size])))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            response = self._workspace().serving_endpoints.query(name=self._endpoint, input=batch)
        except DatabricksError as e:
            raise EmbeddingError(f"embedding request to {self._endpoint!r} failed: {e}") from e

        data = sorted(response.data or [], key=lambda d: d.index or 0)
        if len(data) != len(batch):
            raise EmbeddingError(f"expected {len(batch)} embeddings, got {len(data)}")
        vectors: list[list[float]] = []
        for item in data:
            if item.embedding is None:
                raise EmbeddingError("response item has no embedding")
            if len(item.embedding) != self._dim:
                raise EmbeddingDimensionError(
                    f"{self._endpoint!r} returned {len(item.embedding)} dims, expected {self._dim}"
                )
            vectors.append(list(item.embedding))
        return vectors


class FakeEmbedder:
    """Deterministic, hash-based unit vectors. Identical text gives identical vectors;
    there is no semantic similarity. For tests and offline development only."""

    def __init__(self, dim: int = 1024, model_name: str = "fake-hash") -> None:
        self._dim = dim
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < self._dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend(v / 2**31 for v in struct.unpack(">8i", digest))
            counter += 1
        values = values[: self._dim]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
