"""Analyzer-tier routing tests (C-4 cloud escalation).

The behaviour under test is the *dispatch*: the model string alone decides which engine runs,
and each engine gets the shape it expects. The engines themselves are stubbed — a real call
belongs in a golden lane (and the cloud one costs money), not in unit CI.
"""

from __future__ import annotations

import types

import pytest

from code_context import llm


class _Block:
    """A minimal stand-in for an SDK content block (only `.type` / `.text` are read)."""

    def __init__(self, type_: str, text: str = ""):
        self.type = type_
        self.text = text


@pytest.fixture
def cloud_calls(monkeypatch):
    """Capture the kwargs the cloud tier would send, and return a canned reply."""
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(
            content=[_Block("thinking"), _Block("text", "  A synthesized note.  ")]
        )

    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    llm._cloud_client.cache_clear()
    monkeypatch.setattr(llm, "_cloud_client", lambda timeout_s: client)
    return calls


@pytest.fixture
def ollama_calls(monkeypatch):
    """Capture the payload the local tier would POST, and return a canned reply."""
    calls: list[dict] = []

    def post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"response": "<think>weighing it</think>A local note."},
        )

    monkeypatch.setattr(llm.httpx, "post", post)
    return calls


def test_bare_model_routes_to_ollama(ollama_calls, cloud_calls):
    assert llm.generate("prompt", model="qwen3:8b") == "A local note."
    (call,) = ollama_calls
    assert call["json"]["model"] == "qwen3:8b"
    assert not cloud_calls  # the local path must never construct a cloud client


def test_prefixed_model_routes_to_the_cloud_tier(cloud_calls, ollama_calls):
    assert llm.generate("prompt", model="anthropic:claude-opus-4-8") == "A synthesized note."
    (call,) = cloud_calls
    # The prefix is routing, not part of the model id the API is asked for.
    assert call["model"] == "claude-opus-4-8"
    assert call["thinking"] == {"type": "adaptive"}
    assert not ollama_calls


def test_cloud_call_drops_the_local_thinking_directive(cloud_calls):
    llm.generate("Summarize this module.\n/no_think", model="anthropic:claude-opus-4-8")
    (call,) = cloud_calls
    assert call["messages"] == [{"role": "user", "content": "Summarize this module."}]


def test_system_prompt_is_omitted_rather_than_sent_empty(cloud_calls):
    llm.generate("prompt", model="anthropic:claude-opus-4-8")
    assert "system" not in cloud_calls[0]
    llm.generate("prompt", system="You are an analyzer.", model="anthropic:claude-opus-4-8")
    assert cloud_calls[1]["system"] == "You are an analyzer."


def test_cloud_reply_keeps_only_text_blocks(cloud_calls):
    # The reply carries a thinking block first; a note must not absorb the reasoning.
    assert llm.generate("prompt", model="anthropic:claude-opus-4-8") == "A synthesized note."


def test_rollup_budgets_reach_the_engine_that_understands_them(cloud_calls, ollama_calls):
    llm.generate("prompt", model="qwen3:8b", timeout_s=900, num_ctx=32768)
    assert ollama_calls[0]["timeout"] == 900
    assert ollama_calls[0]["json"]["options"]["num_ctx"] == 32768
    # num_ctx is an Ollama window — the cloud tier sizes its own and must not be handed one.
    llm.generate("prompt", model="anthropic:claude-opus-4-8", timeout_s=900, num_ctx=32768)
    assert "num_ctx" not in cloud_calls[0]
    assert cloud_calls[0]["max_tokens"] == llm.settings.cloud_max_tokens
