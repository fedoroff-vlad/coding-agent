"""Embeddings via the local Ollama engine.

Dev points at the Windows box; prod at the Mac. Model + dim come from :mod:`code_context.config`
(``nomic-embed-text`` → 768). This is the only place that talks to the embed model, so swapping
models later is a one-line config change, not a code change.

Hardened for a constrained (CPU-only) engine: a naive "POST the whole batch of raw fragments"
500s in three ways a code index hits routinely — an oversize class body (nomic's window is ~2048
tokens), an empty fragment (a marker interface), or memory pressure from many large chunks at once.
So inputs are sanitised before they are sent, a batch that still fails is split and retried down to
single items, and a genuine failure surfaces Ollama's own error text instead of a bare 500.
"""

from __future__ import annotations

import logging

import httpx

from . import obs
from .config import settings

#: Character cap per input. Ollama's ``/api/embed`` truncates oversize inputs by default, but some
#: builds 500 instead — so we cap as a backstop. ~4 chars/token keeps this comfortably above nomic's
#: ~2048-token window while still cutting a pathologically large (generated / minified) fragment.
_MAX_INPUT_CHARS = 8000

#: Stand-in for an empty / whitespace-only fragment. Sending "" makes the engine error, and dropping
#: the input would misalign the returned vectors with the batch — so we embed a placeholder to keep
#: that slot. A near-empty fragment carries no retrieval signal anyway; the point is 1:1 alignment.
_EMPTY_PLACEHOLDER = "(empty)"


class EmbedError(RuntimeError):
    """An embed request failed. The message carries Ollama's own error text where available."""


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns one vector per input, **in order**, each of length
    ``settings.embed_dim``.

    Uses Ollama's ``/api/embed`` (batch). Resilient by construction: inputs are sanitised (empty →
    placeholder, oversize → truncated) so one bad fragment can't 500 the whole batch, and a batch
    that still fails is halved and retried down to single items — so a systemic failure (engine
    down, out of memory) fails fast with Ollama's message rather than aborting on the first big
    file. Raises ``ValueError`` on a dimension mismatch (a config error retrying can't fix), so a
    misconfigured model can't silently poison the index.
    """
    if not texts:
        return []
    return _embed_batch([_sanitize(t) for t in texts])


def _sanitize(text: str) -> str:
    """Make one input safe to send: empty → placeholder, oversize → truncated."""
    stripped = text.strip()
    if not stripped:
        return _EMPTY_PLACEHOLDER
    return stripped[:_MAX_INPUT_CHARS]


def _embed_batch(inputs: list[str]) -> list[list[float]]:
    """Embed a sanitised batch, splitting and retrying if the engine rejects it as a whole."""
    try:
        return _embed_once(inputs)
    except (EmbedError, httpx.HTTPError):
        # A single input that still fails is not a batch problem — re-raise with Ollama's text.
        if len(inputs) <= 1:
            raise
    # Split and retry: isolates one poison input and halves peak memory on a weak engine, instead
    # of losing the whole run to one batch. The left half is evaluated first, so a systemic failure
    # (a dead engine) short-circuits after ~log2(n) attempts rather than fanning out.
    mid = len(inputs) // 2
    obs.event("embed.split", logging.DEBUG, count=len(inputs))
    return _embed_batch(inputs[:mid]) + _embed_batch(inputs[mid:])


def _embed_once(inputs: list[str]) -> list[list[float]]:
    """One ``/api/embed`` POST for the given inputs. Raises :class:`EmbedError` on a non-200."""
    with obs.timed("embed.batch", logging.DEBUG, model=settings.embed_model, count=len(inputs)):
        resp = httpx.post(
            f"{settings.ollama_url}/api/embed",
            json={"model": settings.embed_model, "input": inputs, "truncate": True},
            timeout=120,
        )
        if resp.status_code != 200:
            # Surface Ollama's own message — a bare "500 Internal Server Error" hides the cause
            # (oversize input, out of memory, a model that failed to load), which is exactly what
            # left the first real-box failure undiagnosable.
            raise EmbedError(
                f"Ollama /api/embed returned {resp.status_code} for {len(inputs)} input(s): "
                f"{_error_detail(resp)}"
            )
        vectors = resp.json()["embeddings"]
    for v in vectors:
        if len(v) != settings.embed_dim:
            raise ValueError(
                f"embed dim mismatch: model {settings.embed_model!r} returned {len(v)}, "
                f"config expects {settings.embed_dim} (check CODE_CONTEXT_EMBED_DIM + vector(N))"
            )
    return vectors


def _error_detail(resp: httpx.Response) -> str:
    """Ollama's error text: the JSON ``{"error": ...}`` body if present, else the raw body."""
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            return str(body["error"])
    except Exception:  # noqa: BLE001 - a non-JSON error body falls back to the raw text below
        pass
    return (resp.text or "").strip()[:500] or "(no response body)"


def embed_one(text: str) -> list[float]:
    """Convenience wrapper for a single text."""
    return embed([text])[0]


def to_literal(vec: list[float]) -> str:
    """Render a vector as a pgvector text literal (used with a ``%s::vector`` cast)."""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"
