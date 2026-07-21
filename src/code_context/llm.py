"""Text generation for the index-time analyzers — local Ollama, or a cloud escalation tier.

The generative counterpart of :mod:`code_context.embeddings`: the one place that talks to an
*analyzer* model, so swapping models (or escalating leaf→rollup to a stronger tier) is a config
change, not a code change. Model + URL come from :mod:`code_context.config`.

**Which engine runs is carried by the model string itself** (``anthropic:claude-opus-4-8`` → the
cloud tier; any other value → local Ollama). One string, not a second `*_provider` setting, because
the model already feeds the incremental keys (``notes.facts_key`` / ``rollup.inputs_digest``) — a
provider on a separate knob could change underneath them and re-use stale notes silently, which is
exactly the defect the first real-repo run surfaced.

Being the one place a local analyzer model is loaded also makes this the one place the **lifecycle
handshake** belongs (C-6a): :func:`code_context.lifecycle.acquire` runs immediately before the local
POST and nowhere else — the cloud tier loads nothing on the shared Mac, so it never signals.

The pipeline stays model-agnostic (REFERENCE §4.2): richness varies, not whether it runs. Thinking
models (qwen3) emit a ``<think>…</think>`` preamble; we suppress it (``/no_think`` + strip) so
callers get just the note, not the reasoning.
"""

from __future__ import annotations

import functools
import logging
import re

import httpx

from . import lifecycle, obs
from .config import settings

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

#: Model-string prefix that routes a call to the cloud tier (roadmap: Opus for rollups).
#: A prefix rather than "contains a colon" — Ollama tags are ``name:tag`` themselves.
CLOUD_PREFIX = "anthropic:"

#: Engine-specific directive our prompts carry for the local thinking models. It is meaningless
#: to the cloud tier (which returns reasoning as separate blocks), so it is stripped there rather
#: than shipped as literal noise at the end of every prompt.
_LOCAL_DIRECTIVE = re.compile(r"\s*/no_think\s*$")


def generate(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    timeout_s: int | None = None,
    num_ctx: int | None = None,
) -> str:
    """Run one non-streaming completion and return the cleaned text.

    ``model`` overrides the configured analyzer model (used to escalate a rollup to a stronger
    tier, local or cloud). ``timeout_s`` / ``num_ctx`` default to the leaf-note budget; rollups
    override both, since a rollup prompt aggregates many child notes and is inherently larger
    than a leaf's single class (a leaf-sized budget truncates the prompt and times the call out).
    ``num_ctx`` is an Ollama window and is ignored by the cloud tier, which sizes its own.
    Raises on transport error so a dead engine can't silently produce empty notes.
    """
    effective_model = model or settings.notes_model
    effective_timeout = timeout_s if timeout_s is not None else settings.notes_timeout_s
    if effective_model.startswith(CLOUD_PREFIX):
        return _generate_cloud(
            prompt,
            system=system,
            model=effective_model[len(CLOUD_PREFIX) :],
            timeout_s=effective_timeout,
        )
    return _generate_ollama(
        prompt,
        system=system,
        model=effective_model,
        timeout_s=effective_timeout,
        num_ctx=num_ctx if num_ctx is not None else settings.notes_num_ctx,
    )


def _generate_ollama(
    prompt: str, *, system: str | None, model: str, timeout_s: int, num_ctx: int
) -> str:
    """The local tier: one ``/api/generate`` call against the configured Ollama."""
    payload: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        # Deterministic-ish notes: low temperature, no thinking preamble to burn tokens on.
        "think": False,
        "options": {"temperature": 0.2, "num_ctx": num_ctx},
    }
    if system is not None:
        payload["system"] = system

    # Before the POST, because the POST is what loads the model: on a shared Mac ai-life must have
    # finished downshifting first (C-6a). A no-op unless the lifecycle flag is on.
    lifecycle.acquire(model)
    obs.check_context_pressure(len(prompt), num_ctx, model)
    with obs.timed(
        "llm.generate",
        logging.DEBUG,
        provider="ollama",
        model=model,
        prompt_chars=len(prompt),  # size only — the prompt itself is never logged
        num_ctx=num_ctx,
    ) as ev:
        resp = httpx.post(
            f"{settings.ollama_url}/api/generate",
            json=payload,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        text = strip_think(resp.json().get("response", "")).strip()
        ev["response_chars"] = len(text)
    return text


def _generate_cloud(prompt: str, *, system: str | None, model: str, timeout_s: int) -> str:
    """The escalation tier: one Messages API call, for rollup-grade cross-file reasoning.

    Credentials are the SDK's business (``ANTHROPIC_API_KEY`` or a configured profile) — we never
    read, hold or log a key. No context-pressure check: the window here is orders of magnitude
    larger than a rollup prompt, so the local warn would only ever be noise.
    """
    client = _cloud_client(timeout_s)
    kwargs: dict[str, object] = {
        "model": model,
        "max_tokens": settings.cloud_max_tokens,
        # Cross-file synthesis is the whole reason to escalate — let the model decide how much
        # to think rather than pinning a budget.
        "thinking": {"type": "adaptive"},
        "messages": [{"role": "user", "content": _LOCAL_DIRECTIVE.sub("", prompt)}],
    }
    if system is not None:
        kwargs["system"] = system

    with obs.timed(
        "llm.generate",
        logging.DEBUG,
        provider="anthropic",
        model=model,
        prompt_chars=len(prompt),  # size only — the prompt itself is never logged
    ) as ev:
        message = client.messages.create(**kwargs)
        text = "\n".join(b.text for b in message.content if b.type == "text").strip()
        ev["response_chars"] = len(text)
    return text


@functools.lru_cache(maxsize=None)
def _cloud_client(timeout_s: int):
    """The Anthropic client, reused across a run (a rollup pass makes one call per directory)."""
    try:
        import anthropic
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by hand, not in unit CI
        raise RuntimeError(
            f"a '{CLOUD_PREFIX}' model needs the cloud extra: uv sync --extra cloud"
        ) from exc
    return anthropic.Anthropic(timeout=timeout_s)


def strip_think(text: str) -> str:
    """Remove a ``<think>…</think>`` reasoning preamble (qwen3 emits one unless suppressed)."""
    return _THINK_BLOCK.sub("", text)
