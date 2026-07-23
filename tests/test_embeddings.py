"""Unit tests for the embedder's resilience layer (no DB / Ollama — httpx is stubbed).

The behaviour under test is what keeps an index run alive on a constrained engine: inputs are
sanitised before they are sent, a batch the engine rejects is split and retried down to single
items, and a genuine failure surfaces Ollama's own error text (not a bare 500).
"""

from __future__ import annotations

import types

import pytest

from code_context import embeddings


def _resp(status_code: int, payload: dict, text: str = ""):
    """A minimal stand-in for an httpx.Response (only what embeddings reads)."""
    return types.SimpleNamespace(
        status_code=status_code,
        json=lambda: payload,
        text=text,
    )


@pytest.fixture(autouse=True)
def _small_dim(monkeypatch):
    """Use a 3-dim vector so the fixtures stay readable."""
    monkeypatch.setattr(embeddings.settings, "embed_dim", 3)


def _vec():
    return [0.1, 0.2, 0.3]


def test_returns_one_vector_per_input_in_order(monkeypatch):
    def post(url, json, timeout):
        return _resp(200, {"embeddings": [_vec() for _ in json["input"]]})

    monkeypatch.setattr(embeddings.httpx, "post", post)
    assert embeddings.embed(["a", "b", "c"]) == [_vec(), _vec(), _vec()]


def test_empty_input_is_sent_as_a_placeholder_not_dropped(monkeypatch):
    sent: list[list[str]] = []

    def post(url, json, timeout):
        sent.append(json["input"])
        return _resp(200, {"embeddings": [_vec() for _ in json["input"]]})

    monkeypatch.setattr(embeddings.httpx, "post", post)
    out = embeddings.embed(["real", "   "])
    assert len(out) == 2  # both slots kept — alignment with the batch is preserved
    assert "" not in sent[0]  # the blank was replaced, never sent as an empty string
    assert embeddings._EMPTY_PLACEHOLDER in sent[0]


def test_oversize_input_is_truncated_before_sending(monkeypatch):
    sent: list[str] = []

    def post(url, json, timeout):
        sent.extend(json["input"])
        return _resp(200, {"embeddings": [_vec() for _ in json["input"]]})

    monkeypatch.setattr(embeddings.httpx, "post", post)
    embeddings.embed(["x" * (embeddings._MAX_INPUT_CHARS + 5000)])
    assert len(sent[0]) == embeddings._MAX_INPUT_CHARS


def test_a_batch_that_500s_is_split_and_retried_per_item(monkeypatch):
    calls: list[int] = []

    def post(url, json, timeout):
        n = len(json["input"])
        calls.append(n)
        if n > 1:  # the engine only tolerates single items (simulated memory pressure)
            return _resp(500, {"error": "out of memory"})
        return _resp(200, {"embeddings": [_vec()]})

    monkeypatch.setattr(embeddings.httpx, "post", post)
    out = embeddings.embed(["a", "b", "c", "d"])
    assert out == [_vec()] * 4  # every input still embedded, in order
    assert max(calls) == 4 and min(calls) == 1  # it really did split down to singles


def test_a_persistent_single_item_failure_raises_with_ollamas_text(monkeypatch):
    def post(url, json, timeout):
        return _resp(500, {"error": "input length exceeds maximum context length"})

    monkeypatch.setattr(embeddings.httpx, "post", post)
    with pytest.raises(embeddings.EmbedError) as exc:
        embeddings.embed(["a", "b"])
    # The real cause is in the message, not swallowed behind a bare 500.
    assert "input length exceeds maximum context length" in str(exc.value)


def test_error_detail_falls_back_to_raw_body_when_not_json(monkeypatch):
    def post(url, json, timeout):
        def _boom():
            raise ValueError("not json")

        return types.SimpleNamespace(status_code=500, json=_boom, text="upstream connect error")

    monkeypatch.setattr(embeddings.httpx, "post", post)
    with pytest.raises(embeddings.EmbedError) as exc:
        embeddings.embed(["a"])
    assert "upstream connect error" in str(exc.value)


def test_dimension_mismatch_raises_and_is_not_retried(monkeypatch):
    calls: list[int] = []

    def post(url, json, timeout):
        calls.append(1)
        return _resp(200, {"embeddings": [[0.1, 0.2]]})  # 2 dims, config expects 3

    monkeypatch.setattr(embeddings.httpx, "post", post)
    with pytest.raises(ValueError, match="embed dim mismatch"):
        embeddings.embed(["a"])
    assert len(calls) == 1  # a config error is not a transport hiccup — no split/retry storm


def test_empty_list_makes_no_call(monkeypatch):
    def post(url, json, timeout):  # pragma: no cover - must not be reached
        raise AssertionError("no request for an empty batch")

    monkeypatch.setattr(embeddings.httpx, "post", post)
    assert embeddings.embed([]) == []
