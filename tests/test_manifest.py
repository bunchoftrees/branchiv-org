from datetime import date
from pathlib import Path

import pytest

from branchiv_org.ingest.manifest import DEFAULT_MANIFEST, ManifestError, load_manifest, main

VALID = """
snapshot_version = "v1"
as_of = 2026-11-22

[[sources]]
doc_id = "alpha-report"
title = "Alpha Report"
publisher = "Alpha Agency"
source_type = "oversight_report"
tier = 1
published = "2025-03-31"
url = "https://example.org/alpha.pdf"
status = "to_collect"
verified = true
safety_review = false

[[sources]]
doc_id = "beta-notes"
title = "Beta Notes"
publisher = "Beta"
source_type = "analysis"
tier = 4
published = "2025-03"
local_path = "data/sources/beta.md"
format = "markdown"
status = "collected"
verified = true
safety_review = true

[[sources]]
doc_id = "gamma-docket"
title = "Gamma Docket"
publisher = "Gamma Court"
source_type = "court_filing"
tier = 1
status = "to_collect"
verified = true
safety_review = false
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sources.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_manifest_loads_and_maps_to_documents(tmp_path: Path) -> None:
    manifest = load_manifest(_write(tmp_path, VALID))

    docs = manifest.to_documents()

    assert [d.doc_id for d in docs] == ["alpha-report", "beta-notes", "gamma-docket"]
    assert docs[0].tier == 1
    assert docs[0].source_type == "oversight_report"
    assert docs[0].published_date == date(2025, 3, 31)
    assert docs[1].published_date == date(2025, 3, 1)
    assert docs[2].published_date is None
    assert manifest.sources[1].published == "2025-03"
    assert all(d.snapshot_version == "v1" for d in docs)


def test_unquoted_toml_date_is_accepted(tmp_path: Path) -> None:
    text = VALID.replace('published = "2025-03-31"', "published = 2025-03-31")
    manifest = load_manifest(_write(tmp_path, text))
    assert manifest.sources[0].published == "2025-03-31"


def test_repo_manifest_is_valid() -> None:
    manifest = load_manifest(DEFAULT_MANIFEST)
    assert len(manifest.sources) > 0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("tier = 4", "tier = 5"), "tier"),
        (('doc_id = "beta-notes"', 'doc_id = "alpha-report"'), "duplicate"),
        (('doc_id = "beta-notes"', 'doc_id = "Beta Notes"'), "slug"),
        (('local_path = "data/sources/beta.md"', 'local_pth = "x"'), "local_pth"),
        (('local_path = "data/sources/beta.md"', ""), "url or a local_path"),
        (('format = "markdown"', ""), "needs a format"),
        (('format = "markdown"', 'format = "docx"'), "format"),
        (('url = "https://example.org/alpha.pdf"', 'url = "ftp://x"'), "http"),
        (('source_type = "analysis"', 'source_type = "pdf"'), "source_type"),
        (('status = "collected"', 'status = "done"'), "status"),
        (('published = "2025-03"', 'published = "March 2025"'), "YYYY"),
        (('published = "2025-03"', 'published = "2025-13"'), "month"),
        (('published = "2025-03"', 'published = "2025-02-30"'), "day"),
        (("verified = true\nsafety_review = true", "safety_review = true"), "verified"),
        (("as_of = 2026-11-22", ""), "as_of"),
    ],
)
def test_invalid_manifests_are_rejected(
    tmp_path: Path, change: tuple[str, str], message: str
) -> None:
    old, new = change
    assert old in VALID
    with pytest.raises(ManifestError, match=message):
        load_manifest(_write(tmp_path, VALID.replace(old, new)))


def test_needs_original_requires_url(tmp_path: Path) -> None:
    # gamma-docket (last entry) has no url.
    head, gamma = VALID.rsplit("[[sources]]", 1)
    text = head + "[[sources]]" + gamma.replace('"to_collect"', '"needs_original"')
    with pytest.raises(ManifestError, match="needs_original"):
        load_manifest(_write(tmp_path, text))


def test_bad_toml_is_a_manifest_error(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        load_manifest(_write(tmp_path, "snapshot_version = "))


def test_cli_reports_counts_and_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(_write(tmp_path, VALID))]) == 0
    out = capsys.readouterr().out
    assert "3 sources" in out
    assert "tier:   1=2 2=0 3=0 4=1" in out
    assert "to_collect=2 collected=1 unavailable=0 needs_original=0" in out
    assert "safety_review: 1" in out
    assert main([str(_write(tmp_path, VALID.replace("tier = 1", "tier = 0")))]) == 1
