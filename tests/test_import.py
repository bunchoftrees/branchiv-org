from pathlib import Path

import pytest

import branchiv_org
from branchiv_org.config import Settings, get_settings


def test_package_imports() -> None:
    assert branchiv_org.__name__ == "branchiv_org"


def test_settings_load_without_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("LLM_API_KEY", "DATABASE_URL", "DATABRICKS_HOST", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    settings = Settings()

    assert settings.llm_api_key is None
    assert settings.database_url is None
    assert settings.databricks_host is None
    assert settings.databricks_token is None


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
