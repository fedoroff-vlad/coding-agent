"""Embeddings via the local Ollama engine.

Dev points at the Windows box; prod at the Mac. Model + dim come from :mod:`code_context.config`
(``nomic-embed-text`` → 768). This is the only place that talks to the embed model, so swapping
models later is a one-line config change, not a code change.
"""

from __future__ import annotations

import logging

import httpx

from . import obs
from .config import settings


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns one vector per input, each of length ``settings.embed_dim``.

    Uses Ollama's ``/api/embed`` (batch). Raises on transport error or a dimension mismatch, so a
    misconfigured model can't silently poison the index.
    """
    if not texts:
        return []
    with obs.timed("embed.batch", logging.DEBUG, model=settings.embed_model, count=len(texts)):
        resp = httpx.post(
            f"{settings.ollama_url}/api/embed",
            json={"model": settings.embed_model, "input": texts},
            timeout=120,
        )
        resp.raise_for_status()
        vectors = resp.json()["embeddings"]
    for v in vectors:
        if len(v) != settings.embed_dim:
            raise ValueError(
                f"embed dim mismatch: model {settings.embed_model!r} returned {len(v)}, "
                f"config expects {settings.embed_dim} (check CODE_CONTEXT_EMBED_DIM + vector(N))"
            )
    return vectors


def embed_one(text: str) -> list[float]:
    """Convenience wrapper for a single text."""
    return embed([text])[0]


def to_literal(vec: list[float]) -> str:
    """Render a vector as a pgvector text literal (used with a ``%s::vector`` cast)."""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"
