"""The collector: download the manifest's sources and record what was fetched.

For each source with status to_collect or needs_original and a url, the collector
downloads that one url (never links inside the document), saves the bytes as-is to
data/raw/<doc_id>/<timestamp>.<ext>, and records the result in the lock file
(data/manifest/collected.toml). It never edits sources.toml.

It is deliberately polite: a generic User-Agent, at least 2 s between requests to the
same host, a timeout, a size cap, and retries (with backoff) only on 429 and 5xx.
There is no JavaScript rendering; HTML is saved as served.

Usage:
  uv run python -m branchiv_org.ingest.collect --dry-run         # plan only, no network
  uv run python -m branchiv_org.ingest.collect [--doc-id ID ...] [--limit N] [--refresh]
  uv run python -m branchiv_org.ingest.collect import DOC_ID PATH  # a hand-downloaded file
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx2

from branchiv_org.ingest.lock import (
    DEFAULT_LOCK,
    Fetch,
    FetchedFormat,
    Lock,
    LockError,
    Snapshot,
    load_lock,
    save_lock,
)
from branchiv_org.ingest.manifest import DEFAULT_MANIFEST, ManifestError, Source, load_manifest

USER_AGENT = "branchiv-org-collector/0.1"
DEFAULT_RAW_DIR = Path("data/raw")

# Hosts known to be unreachable; their sources are skipped rather than retried.
OFFLINE_HOSTS: dict[str, str] = {
    "apps.dtic.mil": "DTIC public access offline since late summer 2026",
}

FETCH_STATUSES = frozenset({"to_collect", "needs_original"})

_EXTENSIONS: dict[FetchedFormat, str] = {"pdf": "pdf", "html": "html", "text": "txt"}


@dataclass(frozen=True)
class Settings:
    raw_dir: Path = DEFAULT_RAW_DIR
    max_bytes: int = 100 * 1024 * 1024
    timeout_s: float = 30.0
    min_host_interval_s: float = 2.0
    max_retries: int = 3
    backoff_s: float = 2.0
    max_backoff_s: float = 60.0


# ------------------------------------------------------------------ planning


@dataclass(frozen=True)
class Skip:
    doc_id: str
    reason: str


@dataclass
class Plan:
    fetch: list[Source] = field(default_factory=list)
    skip: list[Skip] = field(default_factory=list)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_live_page(source: Source) -> bool:
    return "live_page" in source.caveats


def make_plan(
    sources: Iterable[Source],
    lock: Lock,
    *,
    doc_ids: Iterable[str] = (),
    limit: int | None = None,
    refresh: bool = False,
) -> Plan:
    """Which sources to fetch now, and why the rest are skipped. No network."""
    sources = list(sources)
    wanted = set(doc_ids)
    unknown = wanted - {s.doc_id for s in sources}
    if unknown:
        raise ValueError(f"unknown doc_id(s): {', '.join(sorted(unknown))}")

    plan = Plan()
    for s in sources:
        if wanted and s.doc_id not in wanted:
            continue
        if s.url is None:
            plan.skip.append(Skip(s.doc_id, "no url"))
        elif s.status not in FETCH_STATUSES:
            plan.skip.append(Skip(s.doc_id, f"status is {s.status}"))
        elif (host := host_of(s.url)) in OFFLINE_HOSTS:
            plan.skip.append(Skip(s.doc_id, f"host offline: {host} ({OFFLINE_HOSTS[host]})"))
        elif s.doc_id in lock.sources and not (refresh or s.doc_id in wanted):
            plan.skip.append(Skip(s.doc_id, "already collected (use --refresh to re-fetch)"))
        elif limit is not None and len(plan.fetch) >= limit:
            plan.skip.append(Skip(s.doc_id, f"over --limit {limit}"))
        else:
            plan.fetch.append(s)
    return plan


def describe_plan(plan: Plan) -> str:
    hosts = Counter(host_of(s.url) for s in plan.fetch if s.url)
    lines = [f"Would fetch {len(plan.fetch)} source(s) from {len(hosts)} host(s):"]
    lines += [f"  {n:3d}  {host}" for host, n in sorted(hosts.items(), key=lambda x: (-x[1], x[0]))]
    by_reason: dict[str, list[str]] = defaultdict(list)
    for skip in plan.skip:
        by_reason[skip.reason].append(skip.doc_id)
    lines.append(f"Would skip {len(plan.skip)}:")
    for reason, ids in sorted(by_reason.items(), key=lambda x: (-len(x[1]), x[0])):
        lines.append(f"  {len(ids):3d}  {reason}: {', '.join(ids)}")
    return "\n".join(lines)


# ------------------------------------------------------------------ fetching


def detect_format(content_type: str | None, head: bytes) -> FetchedFormat | None:
    """pdf, html or text, from the bytes first and the Content-Type second."""
    if b"%PDF-" in head[:1024]:
        return "pdf"
    ct = (content_type or "").split(";")[0].strip().lower()
    sniff = head.lstrip()[:64].lower()
    if ct in ("text/html", "application/xhtml+xml") or sniff.startswith(
        (b"<!doctype html", b"<html")
    ):
        return "html"
    if ct.startswith("text/"):
        return "text"
    return None


Result = Literal["new", "unchanged", "changed", "snapshot", "failed"]


@dataclass(frozen=True)
class Outcome:
    doc_id: str
    result: Result
    message: str = ""
    fetch: Fetch | None = None


class FetchFailed(Exception):
    pass


class Collector:
    def __init__(
        self,
        client: httpx2.Client,
        settings: Settings = Settings(),
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._now = now or (lambda: datetime.now(UTC))
        self._last_request: dict[str, float] = {}

    def _wait_for_host(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = last + self.settings.min_host_interval_s - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _retry_delay(self, attempt: int, response: httpx2.Response) -> float:
        retry_after = response.headers.get("retry-after", "")
        delay = (
            float(retry_after) if retry_after.isdigit() else self.settings.backoff_s * (2**attempt)
        )
        return min(delay, self.settings.max_backoff_s)

    def collect(self, source: Source, previous: Fetch | None) -> Outcome:
        assert source.url is not None
        try:
            fetch = self._download(source.doc_id, source.url)
        except FetchFailed as e:
            return Outcome(source.doc_id, "failed", str(e))

        return reconcile(source, previous, fetch)

    def _download(self, doc_id: str, url: str) -> Fetch:
        host = host_of(url)
        for attempt in range(self.settings.max_retries + 1):
            self._wait_for_host(host)
            try:
                with self.client.stream("GET", url) as response:
                    status = response.status_code
                    if status == 429 or status >= 500:
                        if attempt < self.settings.max_retries:
                            self._sleep(self._retry_delay(attempt, response))
                            continue
                        raise FetchFailed(f"HTTP {status} after {attempt + 1} attempts")
                    if status >= 400:
                        raise FetchFailed(f"HTTP {status}")
                    return self._save(doc_id, url, response)
            except httpx2.HTTPError as e:
                raise FetchFailed(f"{type(e).__name__}: {e}") from e
        raise AssertionError("unreachable")

    def _save(self, doc_id: str, url: str, response: httpx2.Response) -> Fetch:
        cap = self.settings.max_bytes
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > cap:
            raise FetchFailed(f"too large: Content-Length {declared} > cap {cap}")

        retrieved_at = self._now().replace(microsecond=0)
        directory = self.settings.raw_dir / doc_id
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f".partial-{os.getpid()}"
        digest = hashlib.sha256()
        size = 0
        head = b""
        try:
            with tmp.open("wb") as out:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > cap:
                        raise FetchFailed(f"too large: over cap {cap} bytes, aborted")
                    if len(head) < 1024:
                        head += chunk[: 1024 - len(head)]
                    digest.update(chunk)
                    out.write(chunk)
            content_type = response.headers.get("content-type")
            fmt = detect_format(content_type, head)
            if fmt is None:
                raise FetchFailed(f"unsupported content type {content_type!r}")
            path = self._unique_path(directory, retrieved_at, _EXTENSIONS[fmt])
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

        return Fetch(
            url=url,
            final_url=str(response.url),
            http_status=response.status_code,
            content_type=content_type,
            format=fmt,
            bytes=size,
            sha256=digest.hexdigest(),
            retrieved_at=retrieved_at,
            local_path=str(path),
        )

    @staticmethod
    def _unique_path(directory: Path, at: datetime, ext: str) -> Path:
        stem = at.strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"{stem}.{ext}"
        n = 1
        while path.exists():
            path = directory / f"{stem}-{n}.{ext}"
            n += 1
        return path


def reconcile(source: Source, previous: Fetch | None, fetch: Fetch) -> Outcome:
    """Compare a new file with the previous one: keep, discard as duplicate, or keep both."""
    if previous is None:
        return Outcome(source.doc_id, "new", fetch.local_path, fetch)
    same = fetch.sha256 == previous.sha256
    if same and not is_live_page(source):
        Path(fetch.local_path).unlink(missing_ok=True)
        return Outcome(source.doc_id, "unchanged", previous.local_path, previous)
    kept = Snapshot(
        sha256=previous.sha256,
        retrieved_at=previous.retrieved_at,
        local_path=previous.local_path,
    )
    fetch = fetch.model_copy(update={"history": [*previous.history, kept]})
    if same:
        return Outcome(
            source.doc_id, "snapshot", f"live page, new snapshot {fetch.local_path}", fetch
        )
    return Outcome(
        source.doc_id,
        "changed",
        f"sha256 changed upstream: {previous.sha256[:12]} -> {fetch.sha256[:12]}; "
        f"kept {previous.local_path} and {fetch.local_path}",
        fetch,
    )


_SUFFIX_TYPES = {
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def import_file(
    source: Source,
    path: Path,
    previous: Fetch | None,
    settings: Settings = Settings(),
    now: Callable[[], datetime] | None = None,
) -> Outcome:
    """Copy a hand-downloaded file into raw/<doc_id>/ and describe it like a fetch."""
    if not path.is_file():
        return Outcome(source.doc_id, "failed", f"no such file: {path}")
    data = path.read_bytes()
    if len(data) > settings.max_bytes:
        cap = settings.max_bytes
        return Outcome(source.doc_id, "failed", f"too large: {len(data)} > cap {cap}")
    content_type = _SUFFIX_TYPES.get(path.suffix.lower())
    fmt = detect_format(content_type, data[:1024])
    if fmt is None:
        return Outcome(source.doc_id, "failed", f"unsupported file type: {path.name}")
    retrieved_at = (now or (lambda: datetime.now(UTC)))().replace(microsecond=0)
    directory = settings.raw_dir / source.doc_id
    directory.mkdir(parents=True, exist_ok=True)
    dest = Collector._unique_path(directory, retrieved_at, _EXTENSIONS[fmt])
    shutil.copyfile(path, dest)
    fetch = Fetch(
        method="manual",
        url=source.url,
        content_type=content_type,
        format=fmt,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        retrieved_at=retrieved_at,
        local_path=str(dest),
    )
    return reconcile(source, previous, fetch)


def make_client(settings: Settings, transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
    return httpx2.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=settings.timeout_s,
        follow_redirects=True,
        transport=transport,
    )


def run(
    plan: Plan,
    lock: Lock,
    collector: Collector,
    lock_path: Path,
    out: Callable[[str], None] = print,
) -> list[Outcome]:
    outcomes = []
    for source in plan.fetch:
        outcome = collector.collect(source, lock.sources.get(source.doc_id))
        outcomes.append(outcome)
        out(f"{outcome.result:9s} {source.doc_id}  {outcome.message}")
        if outcome.fetch is not None and outcome.result != "unchanged":
            lock.sources[source.doc_id] = outcome.fetch
            save_lock(lock, lock_path)  # after each source, so an interrupted run keeps progress
    counts = Counter(o.result for o in outcomes)
    out(
        "done: "
        + " ".join(
            f"{r}={counts[r]}" for r in ("new", "changed", "snapshot", "unchanged", "failed")
        )
    )
    return outcomes


# ------------------------------------------------------------------ CLI


def import_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m branchiv_org.ingest.collect import")
    parser.add_argument("doc_id")
    parser.add_argument("path", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        lock = load_lock(args.lock)
    except (ManifestError, LockError) as e:
        print(e, file=sys.stderr)
        return 2
    source = next((s for s in manifest.sources if s.doc_id == args.doc_id), None)
    if source is None:
        print(f"unknown doc_id: {args.doc_id}", file=sys.stderr)
        return 2
    outcome = import_file(
        source, args.path, lock.sources.get(source.doc_id), Settings(raw_dir=args.raw_dir)
    )
    print(f"{outcome.result:9s} {source.doc_id}  {outcome.message}")
    if outcome.fetch is not None and outcome.result != "unchanged":
        lock.sources[source.doc_id] = outcome.fetch
        save_lock(lock, args.lock)
    return 1 if outcome.result == "failed" else 0


def main(argv: list[str] | None = None, transport: httpx2.BaseTransport | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["import"]:
        return import_main(argv[1:])
    parser = argparse.ArgumentParser(prog="python -m branchiv_org.ingest.collect")
    parser.add_argument("--doc-id", action="append", default=[], help="only these (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; no network")
    parser.add_argument("--limit", type=int, help="fetch at most N sources")
    parser.add_argument("--refresh", action="store_true", help="re-fetch already collected")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--max-mb", type=float, default=100.0, help="size cap per file")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        lock = load_lock(args.lock)
        plan = make_plan(
            manifest.sources, lock, doc_ids=args.doc_id, limit=args.limit, refresh=args.refresh
        )
    except (ManifestError, LockError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2

    if args.dry_run:
        print(describe_plan(plan))
        return 0

    settings = Settings(raw_dir=args.raw_dir, max_bytes=int(args.max_mb * 1024 * 1024))
    with make_client(settings, transport) as client:
        outcomes = run(plan, lock, Collector(client, settings), args.lock)
    return 1 if any(o.result == "failed" for o in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
