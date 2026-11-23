"""The source manifest: the hand-curated list of every document allowed into the corpus.

Ingest only reads sources listed here, so the manifest is the single answer to
"what is in the corpus, where did it come from, and how much do we trust it?".
See data/manifest/README.md for the fields, the tier definitions and how to add a source.

Usage: uv run python -m branchiv_org.ingest.manifest [path]
"""

from __future__ import annotations

import re
import sys
import tomllib
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from branchiv_org.corpus import Document

DEFAULT_MANIFEST = Path("data/manifest/sources.toml")

# What kind of document it is (not its file format).
SourceType = Literal[
    "permit_notice",
    "regulatory_record",
    "press_release",
    "public_notice",
    "court_filing",
    "testimony",
    "legal_code",
    "contract_record",
    "contract_data",
    "oversight_report",
    "crs_report",
    "gov_report",
    "health_assessment",
    "command_history",
    "technical_paper",
    "reference_data",
    "video",
    "news",
    "advocacy",
    "company",
    "commentary",
    "analysis",
]

# File format of the collected copy; picks the parser.
Format = Literal["pdf", "html", "markdown", "text"]

Status = Literal["to_collect", "collected", "unavailable", "needs_original"]

# Lowercase slug: letters, digits, single hyphens. Stable forever once used.
_DOC_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# YYYY, YYYY-MM or YYYY-MM-DD.
_PUBLISHED = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    source_type: SourceType
    tier: int = Field(ge=1, le=4)
    # Kept as a string so the precision (year, month or day) is not lost.
    published: str | None = None
    url: str | None = None
    local_path: str | None = None
    format: Format | None = None
    status: Status
    verified: bool
    safety_review: bool
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    notes: str | None = None

    @field_validator("published", mode="before")
    @classmethod
    def _published_str(cls, v: object) -> object:
        # An unquoted TOML date parses as a date; accept it as a full-precision date.
        return v.isoformat() if isinstance(v, date) else v

    @field_validator("published")
    @classmethod
    def _published_format(cls, v: str | None) -> str | None:
        if v is not None:
            _parse_published(v)  # raises ValueError on a bad format or impossible date
        return v

    @model_validator(mode="after")
    def _check(self) -> Source:
        if not _DOC_ID.match(self.doc_id):
            raise ValueError(f"doc_id {self.doc_id!r} must be a lowercase-hyphenated slug")
        if self.url is not None and not self.url.startswith(("https://", "http://")):
            raise ValueError(f"{self.doc_id}: url must start with http(s)://")
        if self.status == "collected":
            if self.url is None and self.local_path is None:
                raise ValueError(f"{self.doc_id}: a collected source needs a url or a local_path")
            if self.format is None:
                raise ValueError(f"{self.doc_id}: a collected source needs a format")
        elif self.status == "needs_original" and self.url is None:
            raise ValueError(f"{self.doc_id}: needs_original requires the url of the copy")
        return self

    @property
    def published_date(self) -> date | None:
        """First day of the published period (e.g. "2026-11" -> 2026-11-01), for recency."""
        return None if self.published is None else _parse_published(self.published)


def _parse_published(value: str) -> date:
    m = _PUBLISHED.match(value)
    if m is None:
        raise ValueError(f"published {value!r} must be YYYY, YYYY-MM or YYYY-MM-DD")
    year, month, day = m.groups()
    return date(int(year), int(month or 1), int(day or 1))


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Applies to every document ingested from this manifest. Bump it whenever
    # sources are added, removed, or re-fetched, so chunks can be traced to a snapshot.
    snapshot_version: str = Field(min_length=1, max_length=64)
    # The date the manifest's statuses and notes were last checked.
    as_of: date
    sources: list[Source] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> Manifest:
        counts = Counter(s.doc_id for s in self.sources)
        dupes = [doc_id for doc_id, n in counts.items() if n > 1]
        if dupes:
            raise ValueError(f"duplicate doc_id(s): {', '.join(sorted(dupes))}")
        return self

    def to_documents(self) -> list[Document]:
        """Rows for the documents table (not yet added to any session)."""
        return [
            Document(
                doc_id=s.doc_id,
                title=s.title,
                tier=s.tier,
                source_type=s.source_type,
                published_date=s.published_date,
                url=s.url,
                snapshot_version=self.snapshot_version,
            )
            for s in self.sources
        ]


class ManifestError(Exception):
    pass


def load_manifest(path: Path = DEFAULT_MANIFEST) -> Manifest:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ManifestError(f"{path}: {e}") from e
    try:
        return Manifest.model_validate(raw)
    except ValidationError as e:
        raise ManifestError(f"{path}: invalid manifest\n{e}") from e


def summarize(manifest: Manifest) -> str:
    sources = manifest.sources
    tiers = Counter(s.tier for s in sources)
    statuses = Counter(s.status for s in sources)
    return "\n".join(
        [
            f"snapshot {manifest.snapshot_version}, as of {manifest.as_of}, {len(sources)} sources",
            "  tier:   " + " ".join(f"{t}={tiers[t]}" for t in range(1, 5)),
            "  status: " + " ".join(f"{s}={statuses[s]}" for s in get_args(Status)),
            f"  safety_review: {sum(s.safety_review for s in sources)}",
            f"  unverified: {sum(not s.verified for s in sources)}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else DEFAULT_MANIFEST
    try:
        manifest = load_manifest(path)
    except ManifestError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{path}: OK, {summarize(manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
