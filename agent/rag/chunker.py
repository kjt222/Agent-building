from __future__ import annotations

from typing import Iterable


def split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be non-negative")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length:
        end = min(start + chunk_size, length)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == length:
            break
        start = max(0, end - chunk_overlap)
    return chunks


def split_lines(lines: Iterable[str], chunk_size: int, chunk_overlap: int) -> list[str]:
    return split_text("\n".join(lines), chunk_size, chunk_overlap)
