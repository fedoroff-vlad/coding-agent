"""Text generation for the index-time analyzers — local Ollama, or a cloud escalation tier.

The generative counterpart of :mod:`code_context.embeddings`: the one place that talks to an
*analyzer* model, so swapping models (or escalating leaf→rollup to a stronger tier) is a config
change, not a code change. Model + URL come from :mod:`code_context.config`.

**Which engine runs is carried by the model string itself** — ``anthropic:claude-opus-4-8`` → the
Messages API, ``openai:<model>`` → an OpenAI-dialect endpoint (a company gateway; the dialect
ai-life calls ``openai-compatible``), anything else → local Ollama. One string, not a `*_provider`
setting, because
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
import os
import re

import httpx

from . import lifecycle, obs
from .config import settings

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

#: Model-string prefix that routes a call to the cloud tier (roadmap: Opus for rollups).
#: A prefix rather than "contains a colon" — Ollama tags are ``name:tag`` themselves.
CLOUD_PREFIX = "anthropic:"

#: Model-string prefix for an OpenAI-dialect endpoint — in practice a company's own gateway,
#: which typically fronts several models (chat + embedding) on one URL behind one key. Hand-rolled
#: over httpx rather than pulling the OpenAI SDK: it is a single non-streaming POST, and keeping it
#: dependency-free means the work machine needs no extra installed to use its own gateway.
OPENAI_PREFIX = "openai:"

#: Where the key for that endpoint is read from. **Environment only, never a Settings field**: the
#: settings object is the sort of thing that gets printed while debugging, and a secret in it is
#: one ``print(settings)`` away from a log. Read per call, held nowhere.
OPENAI_KEY_ENV = "CODE_CONTEXT_OPENAI_API_KEY"

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
    if effective_model.startswith(OPENAI_PREFIX):
        return _generate_openai(
            prompt,
            system=system,
            model=effective_model[len(OPENAI_PREFIX) :],
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


def _generate_openai(prompt: str, *, system: str | None, model: str, timeout_s: int) -> str:
    """An OpenAI-dialect endpoint: one non-streaming ``POST /chat/completions``.

    Built for a company gateway reached over the corporate network, so nothing here is
    provider-specific beyond the dialect: the base URL, the model name and the key are all
    deployment facts. No local model is loaded, so this path never signals the lifecycle
    handshake, and ``num_ctx`` (an Ollama knob) is not sent — the server owns its window, which
    is also why the local ``llm.context_pressure`` warn cannot apply here.
    """
    base = settings.openai_base_url.rstrip("/")
    if not base:
        raise RuntimeError(
            f"an '{OPENAI_PREFIX}' model needs CODE_CONTEXT_OPENAI_BASE_URL "
            "(the gateway's OpenAI-dialect root, including /v1)"
        )
    messages: list[dict[str, str]] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    # The qwen-specific tail is meaningless over this dialect — and worse than meaningless: it
    # does not suppress thinking here (that is what openai_suppress_thinking is for), so shipping
    # it would just be literal noise at the end of every prompt.
    messages.append({"role": "user", "content": _LOCAL_DIRECTIVE.sub("", prompt)})

    body: dict[str, object] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0.2,  # same deterministic-ish notes the local tier asks for
    }
    if settings.openai_suppress_thinking:
        body["reasoning_effort"] = "none"

    key = os.environ.get(OPENAI_KEY_ENV, "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    with obs.timed(
        "llm.generate",
        logging.DEBUG,
        provider="openai",
        model=model,
        prompt_chars=len(prompt),  # size only — the prompt itself is never logged
    ) as ev:
        resp = httpx.post(
            f"{base}/chat/completions", json=body, headers=headers, timeout=timeout_s
        )
        resp.raise_for_status()
        choice = resp.json()["choices"][0]["message"].get("content") or ""
        # A thinking model may still hand back a <think> block inline; strip it like the local tier.
        text = strip_think(choice).strip()
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
