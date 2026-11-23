"""Parse collected sources into page text, then chunk them.

Reads every source recorded in the lock file (data/manifest/collected.toml), extracts
text locally (no network), and writes:

- data/processed/<snapshot_version>/chunks.jsonl  (git-ignored; one chunk per line)
- data/manifest/parse-report.md                   (committed; per-document summary)

PDFs are read page by page with pypdf; page numbers are 1-based in file order. HTML
goes through trafilatura's main-text extraction and counts as a single page 1. Lines
repeated at the top or bottom of most PDF pages (running headers, footers, page
numbers) are removed. PDFs with almost no extractable text are reported as needing
OCR; nothing is OCR'd yet.

Usage: uv run python -m branchiv_org.ingest.parse [--doc-id ID ...]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from branchiv_org.ingest.chunk import Chunk, ChunkSettings, Page, chunk_pages
from branchiv_org.ingest.lock import DEFAULT_LOCK, Fetch, LockError, load_lock
from branchiv_org.ingest.manifest import (
    DEFAULT_MANIFEST,
    Manifest,
    ManifestError,
    Source,
    load_manifest,
)

DEFAULT_PROCESSED_DIR = Path("data/processed")
DEFAULT_REPORT = Path("data/manifest/parse-report.md")

# Below this many characters per page on average, a PDF is treated as scanned.
MIN_CHARS_PER_PAGE = 100
SHORT_DOC_WORDS = 150
# How many lines at the top and bottom of a page count as header/footer candidates.
EDGE_LINES = 2

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "its",
        "it",
        "this",
        "that",
        "how",
        "what",
        "why",
        "who",
        "when",
        "new",
    ]
)


@dataclass
class ParsedDoc:
    doc_id: str
    format: str
    pages: list[Page]
    title: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(len(p.text.split()) for p in self.pages)

    @property
    def chars(self) -> int:
        return sum(len(p.text) for p in self.pages)


# ------------------------------------------------------------------ text cleanup


def normalize(text: str) -> str:
    """Collapse runs of spaces, trim lines, keep paragraph breaks (one blank line)."""
    lines = [" ".join(line.split()) for line in text.replace("\r", "\n").split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _edge_key(line: str) -> str:
    # "Page 3 of 52" and "Page 4 of 52" should count as the same footer.
    return re.sub(r"\d+", "#", line.lower()).strip()


def strip_repeated_edges(pages: Sequence[str], threshold: float = 0.5) -> list[str]:
    """Remove header/footer lines that appear on more than `threshold` of pages."""
    if len(pages) < 3:
        return list(pages)
    counts: Counter[str] = Counter()
    for text in pages:
        lines = [ln for ln in text.split("\n") if ln.strip()]
        edges = {_edge_key(ln) for ln in lines[:EDGE_LINES] + lines[-EDGE_LINES:]}
        counts.update(k for k in edges if k)
    repeated = {k for k, n in counts.items() if n > threshold * len(pages)}
    if not repeated:
        return list(pages)
    out = []
    for text in pages:
        lines = text.split("\n")
        nonblank = [i for i, ln in enumerate(lines) if ln.strip()]
        edge_idx = set(nonblank[:EDGE_LINES] + nonblank[-EDGE_LINES:])
        kept = [
            ln for i, ln in enumerate(lines) if not (i in edge_idx and _edge_key(ln) in repeated)
        ]
        out.append(normalize("\n".join(kept)))
    return out


# ------------------------------------------------------------------ parsers


def parse_pdf(doc_id: str, path: Path) -> ParsedDoc:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(path)
    warnings: list[str] = []
    texts: list[str] = []
    failed = 0
    for page in reader.pages:
        try:
            texts.append(normalize(page.extract_text() or ""))
        except Exception:  # pypdf raises a variety of errors on damaged pages
            failed += 1
            texts.append("")
    if failed:
        warnings.append(f"text extraction failed on {failed} page(s)")
    texts = strip_repeated_edges(texts)
    pages = [Page(i, t) for i, t in enumerate(texts, start=1)]
    total_chars = sum(len(t) for t in texts)
    if pages and total_chars / len(pages) < MIN_CHARS_PER_PAGE:
        warnings.append(f"no text layer ({total_chars} chars over {len(pages)} pages): needs OCR")
    blank = sum(len(t) < 50 for t in texts)
    mostly_text = pages and total_chars / len(pages) >= MIN_CHARS_PER_PAGE
    if mostly_text and blank >= 3 and blank / len(pages) > 0.1:
        warnings.append(
            f"{blank} of {len(pages)} pages have no text (scans, maps or figures?): "
            "those pages may need OCR"
        )
    title = None
    try:
        meta = reader.metadata
        title = (meta.title or "").strip() or None if meta else None
    except Exception:
        pass
    return ParsedDoc(doc_id, "pdf", pages, title, warnings)


def parse_html(doc_id: str, path: Path) -> ParsedDoc:
    import trafilatura

    logging.getLogger("trafilatura").setLevel(logging.ERROR)
    raw = path.read_bytes().decode("utf-8", "replace")
    text = trafilatura.extract(
        raw, include_comments=False, include_tables=True, include_links=False
    )
    meta = trafilatura.extract_metadata(raw)
    title = meta.title if meta and meta.title else None
    warnings = [] if text else ["no main text found"]
    return ParsedDoc(doc_id, "html", [Page(1, normalize(text or ""))], title, warnings)


def parse_text(doc_id: str, path: Path) -> ParsedDoc:
    text = path.read_bytes().decode("utf-8", "replace")
    return ParsedDoc(doc_id, "text", [Page(1, normalize(text))])


def _significant(words: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]+", words.lower()) if len(w) > 2 and w not in _STOPWORDS
    }


def title_match(source: Source, doc: ParsedDoc) -> float:
    """Share of the manifest title's significant words found in the document's start."""
    wanted = _significant(source.title)
    if not wanted:
        return 1.0
    head = " ".join(p.text for p in doc.pages[:3])[:6000] + " " + (doc.title or "")
    return len(wanted & _significant(head)) / len(wanted)


def parse_source(source: Source, path: Path, fmt: str) -> ParsedDoc:
    if not path.exists():
        return ParsedDoc(source.doc_id, fmt, [], warnings=[f"file not found: {path}"])
    try:
        if fmt == "pdf":
            doc = parse_pdf(source.doc_id, path)
        elif fmt == "html":
            doc = parse_html(source.doc_id, path)
        else:
            doc = parse_text(source.doc_id, path)
    except Exception as e:
        return ParsedDoc(source.doc_id, fmt, [], warnings=[f"could not parse: {e}"])
    if doc.pages and doc.words < SHORT_DOC_WORDS:
        doc.warnings.append(f"very short ({doc.words} words)")
    if doc.words and title_match(source, doc) < 0.3:
        doc.warnings.append("few title words found in the text: check it is the right document")
    return doc


# ------------------------------------------------------------------ run


def collected_files(
    manifest: Manifest, lock_sources: dict[str, Fetch]
) -> Iterable[tuple[Source, Path, str]]:
    for source in manifest.sources:
        fetch = lock_sources.get(source.doc_id)
        if fetch is not None:
            yield source, Path(fetch.local_path), fetch.format
        elif source.status == "collected" and source.local_path and source.format:
            fmt = "text" if source.format == "markdown" else source.format
            yield source, Path(source.local_path), fmt


def write_chunks(chunks: Iterable[Chunk], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as out:
        for c in chunks:
            out.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
            n += 1
    return n


def render_report(
    manifest: Manifest, docs: Sequence[ParsedDoc], chunk_counts: dict[str, int]
) -> str:
    total_pages = sum(len(d.pages) for d in docs)
    total_chunks = sum(chunk_counts.values())
    with_warnings = [d for d in docs if d.warnings]
    ocr = [d.doc_id for d in docs if any("needs OCR" in w for w in d.warnings)]
    lines = [
        "# Parse report",
        "",
        "Generated by `uv run python -m branchiv_org.ingest.parse`. Do not edit by hand.",
        "",
        f"- Snapshot: `{manifest.snapshot_version}`",
        f"- Documents parsed: {len(docs)}",
        f"- Pages: {total_pages}",
        f"- Words: {sum(d.words for d in docs)}",
        f"- Chunks: {total_chunks}",
        f"- Documents with warnings: {len(with_warnings)}",
        f"- Need OCR: {', '.join(f'`{d}`' for d in ocr) if ocr else 'none'}",
        "",
        "Chunk text is in `data/processed/<snapshot>/chunks.jsonl`, which is not in git.",
        "",
    ]
    if with_warnings:
        lines += ["## Warnings", ""]
        for d in with_warnings:
            lines.append(f"- `{d.doc_id}`: {'; '.join(d.warnings)}")
        lines.append("")
    lines += [
        "## All documents",
        "",
        "| doc_id | format | pages | words | chunks | warnings |",
        "|---|---|---:|---:|---:|---|",
    ]
    for d in docs:
        lines.append(
            f"| `{d.doc_id}` | {d.format} | {len(d.pages)} | {d.words} | "
            f"{chunk_counts.get(d.doc_id, 0)} | {len(d.warnings) or ''} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m branchiv_org.ingest.parse")
    parser.add_argument("--doc-id", action="append", default=[], help="only these (repeatable)")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--max-words", type=int, default=ChunkSettings.max_words)
    parser.add_argument("--overlap", type=float, default=ChunkSettings.overlap)
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        lock = load_lock(args.lock)
        settings = ChunkSettings(max_words=args.max_words, overlap=args.overlap)
    except (ManifestError, LockError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2

    wanted = set(args.doc_id)
    docs: list[ParsedDoc] = []
    chunks: list[Chunk] = []
    counts: dict[str, int] = {}
    for source, path, fmt in collected_files(manifest, lock.sources):
        if wanted and source.doc_id not in wanted:
            continue
        doc = parse_source(source, path, fmt)
        docs.append(doc)
        doc_chunks = chunk_pages(source.doc_id, doc.pages, manifest.snapshot_version, settings)
        counts[source.doc_id] = len(doc_chunks)
        chunks.extend(doc_chunks)
        flag = f"  ! {'; '.join(doc.warnings)}" if doc.warnings else ""
        print(f"{source.doc_id}: {len(doc.pages)} pages, {len(doc_chunks)} chunks{flag}")

    out = args.out_dir / manifest.snapshot_version / "chunks.jsonl"
    n = write_chunks(chunks, out)
    if not wanted:  # a partial run should not overwrite the full report
        args.report.write_text(render_report(manifest, docs, counts), encoding="utf-8")
    print(f"{len(docs)} documents, {sum(len(d.pages) for d in docs)} pages, {n} chunks -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
