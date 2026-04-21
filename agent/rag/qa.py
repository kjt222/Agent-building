from __future__ import annotations

from typing import Iterable, Sequence

from ..models import ModelAdapter
from .store import SearchResult

NO_CONTEXT_MESSAGE = "No relevant context found in the knowledge base."


def build_context(results: Sequence[SearchResult], max_context_chars: int) -> str:
    if max_context_chars <= 0:
        return ""
    blocks: list[str] = []
    used = 0
    for idx, item in enumerate(results, start=1):
        source = item.metadata.get("source_path", "")
        header = f"[{idx}] source: {source}\n"
        body = item.text.strip()
        block = f"{header}{body}\n"
        if used + len(block) <= max_context_chars:
            blocks.append(block)
            used += len(block)
            continue
        if not blocks:
            remaining = max_context_chars - len(header) - 1
            if remaining > 0:
                blocks.append(f"{header}{body[:remaining].rstrip()}\n")
            else:
                blocks.append(header.strip())
        break
    return "\n".join(blocks).strip()


def build_prompt(question: str, context: str) -> str:
    instructions = (
        "You are a helpful assistant. Reference materials are provided below for your reference. "
        "If the materials are relevant, use them to enhance your answer and cite sources using [1], [2]. "
        "If the materials are not relevant to the question, answer based on your own knowledge. "
        "Always be helpful and answer the user's question. Respond in the same language as the question."
    )
    return f"{instructions}\n\nReference Materials:\n{context}\n\nQuestion:\n{question}\n\nAnswer:"


def answer_question(
    llm: ModelAdapter,
    question: str,
    results: Sequence[SearchResult],
    max_context_chars: int,
    allow_empty: bool = True,
    mask_fn=None,
    llm_kwargs=None,
) -> str:
    context = build_context(results, max_context_chars=max_context_chars)
    if mask_fn and context:
        context = mask_fn(context)

    # No relevant context: call LLM directly without RAG constraints
    if not context:
        if not allow_empty:
            return NO_CONTEXT_MESSAGE
        if llm_kwargs:
            return llm.chat(question, **llm_kwargs)
        return llm.chat(question)

    # Has context: use reference-style prompt
    prompt = build_prompt(question, context)
    if llm_kwargs:
        return llm.chat(prompt, **llm_kwargs)
    return llm.chat(prompt)
