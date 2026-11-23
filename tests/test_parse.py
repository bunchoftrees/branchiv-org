"""Parser tests on tiny fixtures generated here (no committed binaries, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from branchiv_org.ingest.manifest import Source
from branchiv_org.ingest.parse import (
    main,
    normalize,
    parse_source,
    strip_repeated_edges,
)


def make_pdf(path: Path, pages: list[list[str]], title: str | None = None) -> Path:
    """Write a minimal valid PDF: one Helvetica text line per string, top to bottom."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    pages_id = len(objects) + 1 + 2 * len(pages)  # allocated after page/content pairs
    for lines in pages:
        ops = ["BT", "/F1 11 Tf", "14 TL", "72 760 Td"]
        for line in lines:
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops += [f"({esc}) Tj", "T*"]
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_id, font, content)
            )
        )
    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    assert add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))) == pages_id
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    info = add(b"<< /Title (%s) >>" % title.encode("latin-1")) if title else None

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    trailer = b"<< /Size %d /Root %d 0 R" % (len(objects) + 1, catalog)
    if info:
        trailer += b" /Info %d 0 R" % info
    out += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
    path.write_bytes(bytes(out))
    return path


def source(doc_id: str = "doc", title: str = "Lead azide storage report") -> Source:
    return Source.model_validate(
        {
            "doc_id": doc_id,
            "title": title,
            "publisher": "P",
            "source_type": "gov_report",
            "tier": 2,
            "url": "https://example.org/x",
            "status": "to_collect",
            "verified": True,
            "safety_review": False,
        }
    )


BODY = [
    "Lead azide storage report for the plant.",
    "The drums were stored in a water bath for many years and inspected each quarter.",
    "Inspections found recrystallization in several containers during routine checks.",
] * 4


def test_pdf_pages_text_and_title(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "a.pdf", [BODY, BODY], title="Storage Report")
    doc = parse_source(source(), path, "pdf")
    assert [p.number for p in doc.pages] == [1, 2]
    assert "recrystallization" in doc.pages[0].text
    assert doc.title == "Storage Report"
    assert doc.warnings == []


def test_pdf_repeated_header_and_footer_are_removed(tmp_path: Path) -> None:
    pages = [["AGENCY REPORT 2026", *BODY, f"Page {i} of 4"] for i in range(1, 5)]
    doc = parse_source(source(), make_pdf(tmp_path / "a.pdf", pages), "pdf")
    for page in doc.pages:
        assert "AGENCY REPORT" not in page.text
        assert "of 4" not in page.text
        assert "water bath" in page.text


def test_pdf_without_text_needs_ocr(tmp_path: Path) -> None:
    doc = parse_source(source(), make_pdf(tmp_path / "scan.pdf", [[], [], []]), "pdf")
    assert any("needs OCR" in w for w in doc.warnings)


def test_wrong_document_is_flagged(tmp_path: Path) -> None:
    other = ["Quarterly budget tables for the parks department."] * 40
    doc = parse_source(source(), make_pdf(tmp_path / "a.pdf", [other]), "pdf")
    assert any("right document" in w for w in doc.warnings)


def test_html_main_text_drops_navigation(tmp_path: Path) -> None:
    article = "".join(f"<p>{line}</p>" for line in BODY)
    page = (
        "<html><head><title>Lead azide storage report | News</title></head><body>"
        "<nav><a href='/'>Home</a> <a href='/news'>News</a> <a href='/sports'>Sports</a></nav>"
        f"<article><h1>Lead azide storage report</h1>{article}</article>"
        "<footer>Copyright 2026 Example News. Privacy policy. Terms of use.</footer>"
        "</body></html>"
    )
    path = tmp_path / "a.html"
    path.write_text(page, encoding="utf-8")
    doc = parse_source(source(), path, "html")
    assert len(doc.pages) == 1
    assert "water bath" in doc.pages[0].text
    assert "Sports" not in doc.pages[0].text
    assert doc.title and "Lead azide" in doc.title


def test_short_and_missing_files_warn(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("Lead azide storage report, short.", encoding="utf-8")
    assert any("very short" in w for w in parse_source(source(), path, "text").warnings)
    missing = parse_source(source(), tmp_path / "nope.pdf", "pdf")
    assert missing.pages == [] and "not found" in missing.warnings[0]


def test_normalize_and_strip_helpers() -> None:
    assert normalize("  a   b \r\n\n\n\n c  ") == "a b\n\nc"
    pages = ["HDR\nbody one\nfoot 1", "HDR\nbody two\nfoot 2", "HDR\nbody three\nfoot 3"]
    assert strip_repeated_edges(pages) == ["body one", "body two", "body three"]
    assert strip_repeated_edges(pages[:2]) == pages[:2]  # too few pages to judge


MANIFEST = """
snapshot_version = "v9"
as_of = 2026-11-22

[[sources]]
doc_id = "doc"
title = "Lead azide storage report"
publisher = "P"
source_type = "gov_report"
tier = 2
url = "https://example.org/x"
status = "to_collect"
verified = true
safety_review = false
"""


def test_cli_writes_chunks_and_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pdf = make_pdf(tmp_path / "raw.pdf", [BODY, BODY, BODY])
    (tmp_path / "sources.toml").write_text(MANIFEST, encoding="utf-8")
    (tmp_path / "collected.toml").write_text(
        '[sources."doc"]\nurl = "https://example.org/x"\nfinal_url = "https://example.org/x"\n'
        'http_status = 200\nformat = "pdf"\nbytes = 1\nsha256 = "' + "0" * 64 + '"\n'
        f'retrieved_at = 2026-11-23T03:00:00Z\nlocal_path = "{pdf}"\n',
        encoding="utf-8",
    )
    report = tmp_path / "parse-report.md"
    args = ["--manifest", str(tmp_path / "sources.toml")]
    args += ["--lock", str(tmp_path / "collected.toml")]
    args += ["--out-dir", str(tmp_path / "processed"), "--report", str(report)]
    args += ["--max-words", "60"]

    assert main(args) == 0

    lines = (tmp_path / "processed" / "v9" / "chunks.jsonl").read_text().splitlines()
    chunks = [json.loads(line) for line in lines]
    assert len(chunks) > 1
    assert {c["doc_id"] for c in chunks} == {"doc"}
    assert {c["snapshot_version"] for c in chunks} == {"v9"}
    assert chunks[-1]["page_end"] == 3
    text = report.read_text()
    assert "Documents parsed: 1" in text and "| `doc` | pdf | 3 |" in text
