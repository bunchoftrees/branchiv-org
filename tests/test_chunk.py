import pytest

from branchiv_org.ingest.chunk import ChunkSettings, Page, chunk_pages


def sentences(n: int, words: int = 10, start: int = 0) -> str:
    return " ".join(
        " ".join(f"w{start + i}x{j}" for j in range(words - 1)).capitalize() + " end."
        for i in range(n)
    )


def test_short_document_is_one_chunk() -> None:
    chunks = chunk_pages("doc", [Page(1, "One sentence. Two sentence.")], "v1")
    assert len(chunks) == 1
    c = chunks[0]
    assert (c.doc_id, c.chunk_index, c.page_start, c.page_end) == ("doc", 0, 1, 1)
    assert c.text == "One sentence. Two sentence."
    assert c.snapshot_version == "v1"


def test_empty_document_has_no_chunks() -> None:
    assert chunk_pages("doc", [Page(1, "  \n\n ")], "v1") == []


def test_chunks_respect_size_overlap_and_sentence_boundaries() -> None:
    settings = ChunkSettings(max_words=100, overlap=0.2)
    pages = [Page(1, sentences(30))]  # 300 words, 10-word sentences

    chunks = chunk_pages("doc", pages, "v1", settings)

    assert len(chunks) >= 3
    for c in chunks:
        assert len(c.text.split()) <= 100
        assert c.text.endswith("end.")  # never cut mid-sentence here
    # Consecutive chunks share about 20 words (two sentences).
    for a, b in zip(chunks, chunks[1:], strict=False):
        shared = set(a.text.split()) & set(b.text.split())
        assert 10 <= len(shared) <= 20
    # Every sentence appears somewhere.
    all_text = " ".join(c.text for c in chunks)
    assert "W29x0" in all_text and "W0x0" in all_text
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_page_ranges_are_tracked() -> None:
    pages = [Page(1, sentences(3)), Page(2, sentences(3, start=3)), Page(3, sentences(3, start=6))]

    one = chunk_pages("doc", pages, "v1", ChunkSettings(max_words=100, overlap=0.0))
    assert [(c.page_start, c.page_end) for c in one] == [(1, 3)]

    # Smaller chunks break at the page (paragraph) boundaries.
    split = chunk_pages("doc", pages, "v1", ChunkSettings(max_words=50, overlap=0.0))
    assert [(c.page_start, c.page_end) for c in split] == [(1, 1), (2, 2), (3, 3)]


def test_prefers_paragraph_boundaries() -> None:
    settings = ChunkSettings(max_words=60, overlap=0.0)
    text = sentences(4) + "\n\n" + sentences(4, start=4)  # two 40-word paragraphs

    chunks = chunk_pages("doc", [Page(1, text)], "v1", settings)

    assert chunks[0].text == sentences(4)
    assert chunks[1].text.startswith("W4x0")


def test_overlong_sentence_is_cut() -> None:
    settings = ChunkSettings(max_words=30, overlap=0.0)
    text = " ".join(f"w{i}" for i in range(95)) + "."

    chunks = chunk_pages("doc", [Page(1, text)], "v1", settings)

    assert [len(c.text.split()) for c in chunks] == [30, 30, 30, 5]


def test_deterministic() -> None:
    pages = [Page(1, sentences(50)), Page(2, sentences(50, start=50))]
    assert chunk_pages("d", pages, "v1") == chunk_pages("d", pages, "v1")


def test_bad_settings() -> None:
    with pytest.raises(ValueError):
        ChunkSettings(max_words=5)
    with pytest.raises(ValueError):
        ChunkSettings(overlap=0.6)
