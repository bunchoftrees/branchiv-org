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
from branchiv_org.ingest.lock import Lock, LockError, load_lock

DEFAULT_MANIFEST = Path("data/manifest/sources.toml")

# What kind of document it is (not its file format).
SourceType = Literal[
    "permit_notice",
    "regulatory_record",
    "press_release",
    "public_notice",
    "court_filing",
    "testimony",
    "statement",  # a public statement or social media post by an official
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

# Why a source's copy needs a warning. Shown to users alongside caveat_note.
Caveat = Literal[
    "unofficial_copy",  # third-party upload, not checked against the original
    "archived_copy",  # web archive snapshot
    "advocacy_host",  # official record hosted by an advocacy group
    "live_page",  # content changes; the snapshot is dated
    # a primary record of what someone said; claims inside it are not verified by it
    "statement_claims_unverified",
]

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
    # doc_id of the canonical record this is a copy of. Supersede the copy with the
    # canonical record once that is collected.
    copy_of: str | None = None
    caveats: list[Caveat] = Field(default_factory=list)
    # Shown to users with every citation of this source. `notes` is internal only.
    caveat_note: str | None = Field(default=None, min_length=1)
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
        if self.copy_of is not None:
            if self.copy_of == self.doc_id:
                raise ValueError(f"{self.doc_id}: copy_of cannot point at itself")
            if not self.caveats:
                raise ValueError(f"{self.doc_id}: a copy (copy_of) needs at least one caveat")
        if len(set(self.caveats)) != len(self.caveats):
            raise ValueError(f"{self.doc_id}: duplicate caveats")
        if self.status == "needs_original" and self.url is None:
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
        unknown = [
            f"{s.doc_id} -> {s.copy_of}"
            for s in self.sources
            if s.copy_of is not None and s.copy_of not in counts
        ]
        if unknown:
            raise ValueError(f"copy_of names an unknown doc_id: {', '.join(unknown)}")
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
    caveats = Counter(c for s in sources for c in s.caveats)
    return "\n".join(
        [
            f"snapshot {manifest.snapshot_version}, as of {manifest.as_of}, {len(sources)} sources",
            "  tier:   " + " ".join(f"{t}={tiers[t]}" for t in range(1, 5)),
            "  status: " + " ".join(f"{s}={statuses[s]}" for s in get_args(Status)),
            f"  safety_review: {sum(s.safety_review for s in sources)}",
            f"  unverified: {sum(not s.verified for s in sources)}",
            "  caveats: " + " ".join(f"{c}={caveats[c]}" for c in get_args(Caveat)),
        ]
    )


def effective_status(source: Source, lock: Lock) -> Status:
    """The manifest's status, except that anything in the lock file has been collected."""
    return "collected" if source.doc_id in lock.sources else source.status


def check_lock(manifest: Manifest, lock: Lock) -> list[str]:
    """Problems joining the lock file to the manifest. Empty means consistent."""
    known = {s.doc_id for s in manifest.sources}
    return [
        f"lock file has {doc_id!r}, which is not in the manifest"
        for doc_id in sorted(lock.sources)
        if doc_id not in known
    ]


def summarize_collection(manifest: Manifest, lock: Lock) -> str:
    statuses = Counter(effective_status(s, lock) for s in manifest.sources)
    by_id = {s.doc_id: s for s in manifest.sources}
    url_moved = sorted(
        doc_id
        for doc_id, f in lock.sources.items()
        if doc_id in by_id and by_id[doc_id].url != f.url
    )
    missing = sorted(
        doc_id for doc_id, f in lock.sources.items() if not Path(f.local_path).exists()
    )
    lines = [
        f"  collection (manifest + lock): fetched={len(lock.sources)} "
        + " ".join(f"{s}={statuses[s]}" for s in get_args(Status)),
    ]
    if url_moved:
        lines.append(f"  url changed in manifest since fetch: {', '.join(url_moved)}")
    if missing:
        lines.append(f"  raw file not on this machine: {len(missing)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else DEFAULT_MANIFEST
    lock_path = path.parent / "collected.toml"
    try:
        manifest = load_manifest(path)
        lock = load_lock(lock_path)
    except (ManifestError, LockError) as e:
        print(e, file=sys.stderr)
        return 1
    problems = check_lock(manifest, lock)
    if problems:
        print(f"{lock_path}: " + "; ".join(problems), file=sys.stderr)
        return 1
    print(f"{path}: OK, {summarize(manifest)}")
    print(summarize_collection(manifest, lock))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
