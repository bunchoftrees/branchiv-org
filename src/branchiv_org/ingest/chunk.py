"""Split parsed documents into overlapping chunks for retrieval.

Chunks never cross documents. Size is measured in words as a stand-in for tokens
(about 500 tokens ~ 375-500 English words; the default of 400 words is conservative).
Splits prefer paragraph boundaries, then sentence boundaries, and only cut mid-sentence
when a single sentence is longer than a whole chunk. Output is deterministic: the
same pages and settings always give the same chunks.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[A-Z0-9\"'(\[])")


@dataclass(frozen=True)
class Page:
    """Text of one page (PDF) or of a whole HTML document (page 1)."""

    number: int
    text: str


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    chunk_index: int
    page_start: int
    page_end: int
    text: str
    snapshot_version: str


@dataclass(frozen=True)
class ChunkSettings:
    max_words: int = 400
    overlap: float = 0.15

    def __post_init__(self) -> None:
        if self.max_words < 20:
            raise ValueError("max_words must be at least 20")
        if not 0 <= self.overlap < 0.5:
            raise ValueError("overlap must be in [0, 0.5)")


@dataclass(frozen=True)
class _Unit:
    """A sentence (or a piece of an over-long sentence) and the page it is on."""

    page: int
    words: tuple[str, ...]
    paragraph_start: bool


def _units(pages: Sequence[Page], max_words: int) -> list[_Unit]:
    units: list[_Unit] = []
    for page in pages:
        for paragraph in re.split(r"\n\s*\n", page.text):
            paragraph = " ".join(paragraph.split())
            if not paragraph:
                continue
            first = True
            for sentence in _SENTENCE_END.split(paragraph):
                words = tuple(sentence.split())
                # A sentence longer than a chunk is cut into chunk-sized pieces.
                for i in range(0, len(words), max_words):
                    piece = words[i : i + max_words]
                    if piece:
                        units.append(_Unit(page.number, piece, first))
                        first = False
    return units


def chunk_pages(
    doc_id: str,
    pages: Sequence[Page],
    snapshot_version: str,
    settings: ChunkSettings = ChunkSettings(),
) -> list[Chunk]:
    units = _units(pages, settings.max_words)
    overlap_words = int(settings.max_words * settings.overlap)
    chunks: list[Chunk] = []
    start = 0
    while start < len(units):
        # Take whole units up to max_words (always at least one).
        end = start
        size = 0
        while end < len(units):
            n = len(units[end].words)
            if end > start and size + n > settings.max_words:
                break
            size += n
            end += 1
        # Prefer to end at a paragraph boundary if one falls in the back half of the chunk.
        if end < len(units):
            for cut in range(end - 1, start, -1):
                if units[cut].paragraph_start:
                    if sum(len(u.words) for u in units[start:cut]) >= settings.max_words // 2:
                        end = cut
                    break
        piece = units[start:end]
        chunks.append(
            Chunk(
                doc_id=doc_id,
                chunk_index=len(chunks),
                page_start=piece[0].page,
                page_end=piece[-1].page,
                text=_join(piece),
                snapshot_version=snapshot_version,
            )
        )
        if end >= len(units):
            break
        # Step back over whole units to overlap by roughly overlap_words, but always advance.
        next_start = end
        back = 0
        while next_start - 1 > start and back + len(units[next_start - 1].words) <= overlap_words:
            next_start -= 1
            back += len(units[next_start].words)
        start = next_start
    return chunks


def _join(units: Sequence[_Unit]) -> str:
    parts: list[str] = []
    for i, unit in enumerate(units):
        sentence = " ".join(unit.words)
        if i == 0:
            parts.append(sentence)
        else:
            parts.append(("\n\n" if unit.paragraph_start else " ") + sentence)
    return "".join(parts)
