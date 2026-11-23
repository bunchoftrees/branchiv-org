"""Collector tests. All HTTP goes through httpx2.MockTransport; nothing touches the network."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from branchiv_org.ingest.collect import (
    USER_AGENT,
    Collector,
    Settings,
    describe_plan,
    detect_format,
    main,
    make_client,
    make_plan,
    run,
)
from branchiv_org.ingest.lock import Fetch, Lock, load_lock
from branchiv_org.ingest.manifest import Source, check_lock, effective_status, load_manifest

PDF = b"%PDF-1.7\n" + b"x" * 2000
PDF_TYPE = {"content-type": "application/pdf"}
HTML = b"<!DOCTYPE html><html><body>hello</body></html>"

Handler = Callable[[httpx2.Request], httpx2.Response]


def source(
    doc_id: str = "alpha", url: str | None = "https://a.example/doc", **kw: object
) -> Source:
    fields: dict[str, object] = {
        "doc_id": doc_id,
        "title": doc_id,
        "publisher": "P",
        "source_type": "news",
        "tier": 3,
        "url": url,
        "status": "to_collect",
        "verified": True,
        "safety_review": False,
    }
    fields.update(kw)
    return Source.model_validate(fields)


class FakeTime:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []
        self.wall = datetime(2026, 11, 23, 3, 0, 0, tzinfo=UTC)

    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s

    def now(self) -> datetime:
        self.wall += timedelta(seconds=1)
        return self.wall


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


def collector(
    handler: Handler, tmp_path: Path, fake_time: FakeTime, **settings: object
) -> Collector:
    s = Settings(raw_dir=tmp_path / "raw", **settings)  # type: ignore[arg-type]
    client = make_client(s, httpx2.MockTransport(handler))
    return Collector(client, s, clock=fake_time.clock, sleep=fake_time.sleep, now=fake_time.now)


def serve(body: bytes, content_type: str, status: int = 200) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, content=body, headers={"content-type": content_type})

    return handler


# ------------------------------------------------------------------ fetching


def test_pdf_is_saved_with_provenance(tmp_path: Path, fake_time: FakeTime) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=PDF, headers={"content-type": "application/pdf"})

    outcome = collector(handler, tmp_path, fake_time).collect(source(), None)

    assert outcome.result == "new"
    fetch = outcome.fetch
    assert fetch is not None
    assert fetch.format == "pdf"
    assert fetch.bytes == len(PDF)
    assert fetch.http_status == 200
    assert fetch.final_url == "https://a.example/doc"
    path = Path(fetch.local_path)
    assert path.parent == tmp_path / "raw" / "alpha"
    assert path.name == "20261123T030001Z.pdf"
    assert path.read_bytes() == PDF
    assert seen[0].headers["user-agent"] == USER_AGENT
    assert list(path.parent.iterdir()) == [path]  # no partial file left behind


def test_html_is_saved_as_is(tmp_path: Path, fake_time: FakeTime) -> None:
    handler = serve(HTML, "text/html; charset=utf-8")
    outcome = collector(handler, tmp_path, fake_time).collect(source(), None)
    assert outcome.fetch is not None
    assert outcome.fetch.format == "html"
    assert outcome.fetch.local_path.endswith(".html")
    assert Path(outcome.fetch.local_path).read_bytes() == HTML


def test_redirect_is_followed_and_final_url_recorded(tmp_path: Path, fake_time: FakeTime) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/doc":
            return httpx2.Response(301, headers={"location": "https://b.example/final.pdf"})
        return httpx2.Response(200, content=PDF, headers={"content-type": "application/pdf"})

    outcome = collector(handler, tmp_path, fake_time).collect(source(), None)
    assert outcome.fetch is not None
    assert outcome.fetch.url == "https://a.example/doc"
    assert outcome.fetch.final_url == "https://b.example/final.pdf"


def test_404_fails_without_retry(tmp_path: Path, fake_time: FakeTime) -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(404, content=b"nope", headers={"content-type": "text/html"})

    outcome = collector(handler, tmp_path, fake_time).collect(source(), None)
    assert outcome.result == "failed"
    assert "HTTP 404" in outcome.message
    assert calls == 1
    assert not (tmp_path / "raw" / "alpha").exists()


def test_429_then_200_retries_with_retry_after(tmp_path: Path, fake_time: FakeTime) -> None:
    responses = [
        httpx2.Response(429, headers={"retry-after": "7"}),
        httpx2.Response(503),
        httpx2.Response(200, content=PDF, headers={"content-type": "application/pdf"}),
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return responses.pop(0)

    outcome = collector(handler, tmp_path, fake_time).collect(source(), None)
    assert outcome.result == "new"
    # Retry-After honored (7 s), then exponential backoff (2 s * 2**1 = 4 s); the
    # per-host interval is already satisfied by those waits.
    assert fake_time.sleeps == [7.0, 4.0]


def test_persistent_5xx_gives_up(tmp_path: Path, fake_time: FakeTime) -> None:
    handler = serve(b"", "text/plain", status=500)
    outcome = collector(handler, tmp_path, fake_time, max_retries=2).collect(source(), None)
    assert outcome.result == "failed"
    assert "after 3 attempts" in outcome.message


def test_oversize_by_content_length_is_refused(tmp_path: Path, fake_time: FakeTime) -> None:
    handler = serve(PDF, "application/pdf")  # MockTransport sets Content-Length
    outcome = collector(handler, tmp_path, fake_time, max_bytes=100).collect(source(), None)
    assert outcome.result == "failed"
    assert "too large" in outcome.message


def test_oversize_stream_is_aborted_and_cleaned_up(tmp_path: Path, fake_time: FakeTime) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        # A streamed body has no Content-Length, so the cap is enforced while reading.
        return httpx2.Response(200, content=iter([PDF[:1000], PDF[1000:]]))

    outcome = collector(handler, tmp_path, fake_time, max_bytes=1500).collect(source(), None)
    assert outcome.result == "failed"
    assert "aborted" in outcome.message
    assert not any((tmp_path / "raw" / "alpha").iterdir())


def test_unsupported_content_is_not_kept(tmp_path: Path, fake_time: FakeTime) -> None:
    handler = serve(b"\x89PNG....", "image/png")
    outcome = collector(handler, tmp_path, fake_time).collect(source(), None)
    assert outcome.result == "failed"
    assert "unsupported" in outcome.message
    assert not any((tmp_path / "raw" / "alpha").iterdir())


def test_transport_error_is_a_failure(tmp_path: Path, fake_time: FakeTime) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    outcome = collector(handler, tmp_path, fake_time).collect(source(), None)
    assert outcome.result == "failed"
    assert "ConnectError" in outcome.message


def test_same_host_requests_are_spaced(tmp_path: Path, fake_time: FakeTime) -> None:
    c = collector(serve(PDF, "application/pdf"), tmp_path, fake_time)
    c.collect(source("one", "https://a.example/1"), None)
    c.collect(source("two", "https://b.example/2"), None)  # other host: no wait
    c.collect(source("three", "https://a.example/3"), None)
    assert fake_time.sleeps == [2.0]


# ------------------------------------------------------------- re-fetching


def test_unchanged_refetch_keeps_one_file(tmp_path: Path, fake_time: FakeTime) -> None:
    c = collector(serve(PDF, "application/pdf"), tmp_path, fake_time)
    first = c.collect(source(), None)
    assert first.fetch is not None
    again = c.collect(source(), first.fetch)
    assert again.result == "unchanged"
    assert again.fetch == first.fetch
    assert list((tmp_path / "raw" / "alpha").iterdir()) == [Path(first.fetch.local_path)]


def test_hash_change_keeps_both_files_and_reports(tmp_path: Path, fake_time: FakeTime) -> None:
    bodies = [PDF, PDF + b"revised"]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=bodies.pop(0), headers=PDF_TYPE)

    c = collector(handler, tmp_path, fake_time)
    first = c.collect(source(), None)
    assert first.fetch is not None
    second = c.collect(source(), first.fetch)

    assert second.result == "changed"
    assert "changed upstream" in second.message
    assert second.fetch is not None
    assert second.fetch.sha256 != first.fetch.sha256
    assert [h.sha256 for h in second.fetch.history] == [first.fetch.sha256]
    assert Path(first.fetch.local_path).exists()
    assert Path(second.fetch.local_path).exists()


def test_live_page_always_keeps_a_new_snapshot(tmp_path: Path, fake_time: FakeTime) -> None:
    live = source(caveats=["live_page"])
    c = collector(serve(HTML, "text/html"), tmp_path, fake_time)
    first = c.collect(live, None)
    assert first.fetch is not None
    second = c.collect(live, first.fetch)
    assert second.result == "snapshot"
    assert second.fetch is not None
    assert second.fetch.local_path != first.fetch.local_path
    assert len(list((tmp_path / "raw" / "alpha").iterdir())) == 2


# ------------------------------------------------------------------ planning


def test_plan_skips_with_reasons() -> None:
    lock = Lock()
    sources = [
        source("fetch-me"),
        source("orig", status="needs_original"),
        source("no-url", url=None),
        source("done", status="collected", local_path="x.pdf", format="pdf"),
        source("gone", status="unavailable"),
        source("dtic", url="https://apps.dtic.mil/sti/pdfs/X.pdf"),
        source("have-it"),
    ]
    lock.sources["have-it"] = _fetch()

    plan = make_plan(sources, lock)

    assert [s.doc_id for s in plan.fetch] == ["fetch-me", "orig"]
    reasons = {s.doc_id: s.reason for s in plan.skip}
    assert reasons["no-url"] == "no url"
    assert reasons["done"] == "status is collected"
    assert reasons["gone"] == "status is unavailable"
    assert reasons["dtic"].startswith("host offline: apps.dtic.mil")
    assert reasons["have-it"].startswith("already collected")

    assert "have-it" in [s.doc_id for s in make_plan(sources, lock, refresh=True).fetch]
    assert [s.doc_id for s in make_plan(sources, lock, doc_ids=["have-it"]).fetch] == ["have-it"]
    limited = make_plan(sources, lock, limit=1)
    assert [s.doc_id for s in limited.fetch] == ["fetch-me"]
    with pytest.raises(ValueError, match="unknown"):
        make_plan(sources, lock, doc_ids=["nope"])

    text = describe_plan(plan)
    assert "Would fetch 2 source(s) from 1 host(s):" in text
    assert "a.example" in text


def _fetch() -> Fetch:
    return Fetch(
        url="https://a.example/doc",
        final_url="https://a.example/doc",
        http_status=200,
        format="pdf",
        bytes=1,
        sha256="0" * 64,
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        local_path="data/raw/have-it/x.pdf",
    )


def test_detect_format() -> None:
    assert detect_format("application/octet-stream", PDF) == "pdf"
    assert detect_format(None, b"  <!doctype html>") == "html"
    assert detect_format("text/plain", b"hello") == "text"
    assert detect_format("image/png", b"\x89PNG") is None


# ------------------------------------------------------------ end to end CLI

MANIFEST = """
snapshot_version = "v1"
as_of = 2026-11-22

[[sources]]
doc_id = "alpha"
title = "Alpha"
publisher = "P"
source_type = "news"
tier = 3
url = "https://a.example/doc"
status = "to_collect"
verified = true
safety_review = false

[[sources]]
doc_id = "broken"
title = "Broken"
publisher = "P"
source_type = "news"
tier = 3
url = "https://a.example/missing"
status = "to_collect"
verified = true
safety_review = false
"""


def test_cli_run_writes_lock_and_validator_joins_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "sources.toml"
    manifest_path.write_text(MANIFEST, encoding="utf-8")
    lock_path = tmp_path / "collected.toml"
    monkeypatch.setattr("branchiv_org.ingest.collect.time.sleep", lambda s: None)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/doc":
            return httpx2.Response(200, content=PDF, headers={"content-type": "application/pdf"})
        return httpx2.Response(404)

    args = ["--manifest", str(manifest_path), "--lock", str(lock_path)]
    args += ["--raw-dir", str(tmp_path / "raw")]
    assert main(args, transport=httpx2.MockTransport(handler)) == 1  # one failure
    out = capsys.readouterr().out
    assert "new=1" in out and "failed=1" in out

    lock = load_lock(lock_path)
    assert list(lock.sources) == ["alpha"]
    assert lock.sources["alpha"].format == "pdf"

    manifest = load_manifest(manifest_path)
    assert check_lock(manifest, lock) == []
    assert effective_status(manifest.sources[0], lock) == "collected"
    assert effective_status(manifest.sources[1], lock) == "to_collect"

    # Second run: alpha is skipped as already collected; the dry run says so.
    assert main([*args, "--dry-run"]) == 0
    assert "already collected" in capsys.readouterr().out


def test_lock_round_trips_with_history(tmp_path: Path, fake_time: FakeTime) -> None:
    bodies = [PDF, PDF + b"2"]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=bodies.pop(0), headers=PDF_TYPE)

    c = collector(handler, tmp_path, fake_time)
    lock = Lock()
    lock_path = tmp_path / "collected.toml"
    plan = make_plan([source()], lock)
    run(plan, lock, c, lock_path, out=lambda _: None)
    run(make_plan([source()], lock, refresh=True), lock, c, lock_path, out=lambda _: None)

    loaded = load_lock(lock_path)
    assert loaded == lock
    assert len(loaded.sources["alpha"].history) == 1


def test_validator_rejects_orphan_lock_entries(tmp_path: Path) -> None:
    manifest_path = tmp_path / "sources.toml"
    manifest_path.write_text(MANIFEST, encoding="utf-8")
    (tmp_path / "collected.toml").write_text(
        '[sources."ghost"]\nurl = "https://x"\nfinal_url = "https://x"\nhttp_status = 200\n'
        'format = "pdf"\nbytes = 1\nsha256 = "' + "0" * 64 + '"\n'
        'retrieved_at = 2026-11-23T03:00:00Z\nlocal_path = "data/raw/ghost/x.pdf"\n',
        encoding="utf-8",
    )
    from branchiv_org.ingest.manifest import main as validate

    assert validate([str(manifest_path)]) == 1
