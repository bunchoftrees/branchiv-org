"""The collection lock file: what the collector actually downloaded for each source.

sources.toml says what *should* be in the corpus; data/manifest/collected.toml records
what *was* fetched (url, final url, checksum, time, where the file is). The raw files
live in data/raw/, which is git-ignored; the lock file is committed, so provenance is
in git even though the bytes are not. A source counts as collected when it has an
entry here (or is marked collected in the manifest, e.g. a hand-added file).
"""

from __future__ import annotations

import json
import os
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_LOCK = Path("data/manifest/collected.toml")

FetchedFormat = Literal["pdf", "html", "text"]

# http: downloaded by the collector. manual: downloaded by a person and imported.
Method = Literal["http", "manual"]


class Snapshot(BaseModel):
    """An earlier download kept on disk (a live page, or a document that changed upstream)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieved_at: datetime
    local_path: str


class Fetch(BaseModel):
    """The latest download of one source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Method = "http"
    # For manual imports, url is the manifest url (if any) and there is no HTTP response.
    url: str | None = None
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    format: FetchedFormat
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieved_at: datetime
    local_path: str
    history: list[Snapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def _http_fields(self) -> Fetch:
        if self.method == "http" and None in (self.url, self.final_url, self.http_status):
            raise ValueError("an http fetch needs url, final_url and http_status")
        return self


class Lock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: dict[str, Fetch] = Field(default_factory=dict)


class LockError(Exception):
    pass


def load_lock(path: Path = DEFAULT_LOCK) -> Lock:
    """The lock file, or an empty lock if it does not exist yet."""
    if not path.exists():
        return Lock()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        return Lock.model_validate(raw)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise LockError(f"{path}: {e}") from e
    except ValidationError as e:
        raise LockError(f"{path}: invalid lock file\n{e}") from e


def _toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat().replace("+00:00", "Z")
    return json.dumps(str(v), ensure_ascii=False)


def dump_lock(lock: Lock) -> str:
    lines = [
        "# Written by the collector (python -m branchiv_org.ingest.collect). Do not edit by hand.",
        "# One table per doc_id: the latest download. Earlier snapshots are kept in `history`.",
        "# The files themselves are in data/raw/, which is not in git.",
    ]
    for doc_id in sorted(lock.sources):
        fetch = lock.sources[doc_id]
        lines += ["", f"[sources.{json.dumps(doc_id)}]"]
        for key, value in fetch.model_dump(exclude={"history"}).items():
            if value is not None:
                lines.append(f"{key} = {_toml_value(value)}")
        if fetch.history:
            lines.append("history = [")
            for snap in fetch.history:
                fields = ", ".join(f"{k} = {_toml_value(v)}" for k, v in snap.model_dump().items())
                lines.append(f"  {{ {fields} }},")
            lines.append("]")
    return "\n".join(lines) + "\n"


def save_lock(lock: Lock, path: Path = DEFAULT_LOCK) -> None:
    """Write atomically, so an interrupted run never leaves a half-written lock file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dump_lock(lock), encoding="utf-8")
    os.replace(tmp, path)
