"""Application settings loaded from environment variables and an optional .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Anthropic API key; the app calls Claude directly through the Anthropic API.
    llm_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    databricks_host: str | None = None
    databricks_token: SecretStr | None = None

    generation_model: str = "claude-opus-5-5"
    check_model: str = "claude-haiku-5-5"  # scope / sufficiency checks
    judge_model: str = "claude-opus-5-5"
    generation_effort: Effort = "medium"

    # Databricks-served embedding model (GTE Large En v1.5; confirmed READY and 1024-dim
    # on 2026-11-23). EMBEDDING_DIM must match the vector column in the database
    # (migration 0002); changing models needs a new migration + re-embed.
    embedding_endpoint: str = "databricks-gte-large-en"
    embedding_dim: int = Field(default=1024, ge=1)
    embedding_batch_size: int = Field(default=32, ge=1)

    # A single refusal must never end a session, so the threshold is at least 2.
    refusal_escalation_threshold: int = Field(default=3, ge=2)
    refusal_window_turns: int = Field(default=10, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
