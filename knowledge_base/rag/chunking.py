from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

from django.conf import settings

class RagChunkingError(Exception):
    pass


@dataclass(frozen=True)
class ChunkConfig:
    chunk_size_chars: int
    overlap_chars: int
    max_chunks_per_document: int

    @property
    def signature(self) -> str:
        raw = f"{self.chunk_size_chars}:{self.overlap_chars}:{self.max_chunks_per_document}"
        return sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChunkRecord:
    ordinal: int
    text: str
    chunk_sha256: str
    start_char: int
    end_char: int
    char_count: int
    byte_count: int


def load_chunk_config() -> ChunkConfig:
    size = int(getattr(settings, "LIVIA_RAG_CHUNK_SIZE_CHARS", 1200) or 0)
    overlap = int(getattr(settings, "LIVIA_RAG_CHUNK_OVERLAP_CHARS", 120) or 0)
    max_chunks = int(getattr(settings, "LIVIA_RAG_MAX_CHUNKS_PER_DOCUMENT", 400) or 0)
    if size <= 0:
        raise RagChunkingError("LIVIA_RAG_CHUNK_SIZE_CHARS must be a positive integer.")
    if overlap < 0:
        raise RagChunkingError("LIVIA_RAG_CHUNK_OVERLAP_CHARS must be zero or positive.")
    if overlap >= size:
        raise RagChunkingError("LIVIA_RAG_CHUNK_OVERLAP_CHARS must be smaller than LIVIA_RAG_CHUNK_SIZE_CHARS.")
    if max_chunks <= 0:
        raise RagChunkingError("LIVIA_RAG_MAX_CHUNKS_PER_DOCUMENT must be a positive integer.")
    return ChunkConfig(
        chunk_size_chars=size,
        overlap_chars=overlap,
        max_chunks_per_document=max_chunks,
    )


def build_deterministic_chunks(text: str, config: ChunkConfig) -> list[ChunkRecord]:
    normalized = str(text or "")
    if not normalized.strip():
        return []
    if len(normalized) <= config.chunk_size_chars:
        return [_make_record(0, normalized, 0, len(normalized))]

    boundaries = _candidate_boundaries(normalized)
    chunks: list[ChunkRecord] = []
    start = 0
    total = len(normalized)
    guard = 0
    while start < total:
        guard += 1
        if guard > max(config.max_chunks_per_document * 5, 1000):
            raise RagChunkingError("Chunking loop guard triggered due to invalid overlap progression.")
        preferred_end = min(start + config.chunk_size_chars, total)
        end = _pick_best_end(boundaries, start, preferred_end, total)
        if end <= start:
            end = min(start + config.chunk_size_chars, total)
        chunk_text = normalized[start:end].strip()
        if chunk_text:
            chunks.append(_make_record(len(chunks), chunk_text, start, end))
        if len(chunks) > config.max_chunks_per_document:
            raise RagChunkingError("Maximum chunks per document exceeded.")
        if end >= total:
            break
        next_start = max(end - config.overlap_chars, start + 1)
        start = next_start

    if any(not item.text for item in chunks):
        raise RagChunkingError("Chunking generated empty chunks, aborting.")
    return chunks


def _candidate_boundaries(text: str) -> list[int]:
    boundaries = {0, len(text)}
    for match in re.finditer(r"\n\n+", text):
        boundaries.add(match.start() + 1)
    for match in re.finditer(r"(?<=[\.\!\?\;\:])\s+", text):
        boundaries.add(match.end())
    return sorted(boundaries)


def _pick_best_end(boundaries: list[int], start: int, preferred_end: int, total: int) -> int:
    inside = [point for point in boundaries if start < point <= preferred_end]
    if inside:
        return max(inside)
    fallback = [point for point in boundaries if point > preferred_end]
    if fallback:
        candidate = min(fallback)
        if candidate - start <= int(preferred_end - start) * 2:
            return candidate
    return min(preferred_end, total)


def _make_record(ordinal: int, text: str, start: int, end: int) -> ChunkRecord:
    chunk_hash = sha256(text.encode("utf-8")).hexdigest()
    return ChunkRecord(
        ordinal=ordinal,
        text=text,
        chunk_sha256=chunk_hash,
        start_char=max(start, 0),
        end_char=max(end, 0),
        char_count=len(text),
        byte_count=len(text.encode("utf-8")),
    )
