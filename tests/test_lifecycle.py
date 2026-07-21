"""The C-6a lifecycle handshake — ordering is the whole contract.

What is under test is not "an HTTP call happens" but **when** it happens relative to the model
load, in both directions. ai-life's LC-4 spec makes that ordering a correctness requirement on a
64 GB box (`../ai-life/plans/lifecycle.md`): signal → *confirmed* downshift → load; unload →
*confirmed* gone → signal normal. Get it backwards and both models are resident at once, which is
the crash the whole mechanism exists to prevent — so the guarantee lives here, in the suite,
rather than in a comment.

`llm` and `lifecycle` both `import httpx`, i.e. the same module object, so one stubbed `post`
records *every* outbound call of both in one ordered list. That is exactly the view these
assertions need.
"""

from __future__ import annotations

import types

import httpx
import pytest

from code_context import lifecycle, llm
from code_context.lifecycle import LifecycleError

PROFILE_URL = "http://localhost:8081/v1/model-profile"
GENERATE_URL = "http://localhost:11434/api/generate"
PS_URL = "http://localhost:11434/api/ps"


def _resp(payload: dict | None = None, *, ok: bool = True):
    def raise_for_status():
        if not ok:
            raise httpx.HTTPError("404 Not Found")

    return types.SimpleNamespace(raise_for_status=raise_for_status, json=lambda: payload or {})


class _Engine:
    """A stand-in for ai-life's gateway + the local Ollama, recording the call order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.profile_ok = True
        self.restore_ok = True
        self.resident: list[str] = []  # what /api/ps reports as loaded

    # -- the two verbs the code under test uses -------------------------------------------
    def post(self, url, json=None, timeout=None):
        body = json or {}
        if url == PROFILE_URL:
            profile = body["profile"]
            self.calls.append(("signal", profile))
            ok = self.profile_ok if profile == lifecycle.CODER_ACTIVE else self.restore_ok
            return _resp(ok=ok)
        if url == GENERATE_URL and "prompt" not in body:
            self.calls.append(("unload", body["model"]))
            assert body["keep_alive"] == 0, "an unload is keep_alive:0, not a normal call"
            return _resp()
        if url == GENERATE_URL:
            self.calls.append(("load", body["model"]))
            self.resident.append(body["model"])
            return _resp({"response": "A local note."})
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, timeout=None):
        assert url == PS_URL
        self.calls.append(("ps", tuple(self.resident)))
        return _resp({"models": [{"model": m} for m in self.resident]})

    # -- readers used by the assertions ---------------------------------------------------
    def actions(self) -> list[str]:
        return [kind for kind, _ in self.calls]


@pytest.fixture(autouse=True)
def engine(monkeypatch):
    """Stub the network, reset the module state, and keep the polling loop instant."""
    eng = _Engine()
    monkeypatch.setattr(httpx, "post", eng.post)
    monkeypatch.setattr(httpx, "get", eng.get)
    monkeypatch.setattr(lifecycle, "_POLL_INTERVAL_S", 0)
    # Off by default (the shipped default) and no idle timer unless a test asks for one.
    monkeypatch.setattr(lifecycle.settings, "lifecycle_enabled", False)
    monkeypatch.setattr(lifecycle.settings, "lifecycle_idle_ttl_s", 0)
    lifecycle._reset()
    yield eng
    lifecycle._reset()


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(lifecycle.settings, "lifecycle_enabled", True)


def test_disabled_by_default_never_talks_to_ai_life(engine):
    """The flag-off case must leave this repo standalone — not one call, not one wait."""
    assert llm.generate("prompt", model="qwen3:8b") == "A local note."
    assert engine.actions() == ["load"]
    assert not lifecycle.active()


def test_the_signal_precedes_the_model_load(engine, enabled):
    """The binding LC-4 ordering: ai-life confirms the downshift BEFORE we load anything."""
    llm.generate("prompt", model="qwen3-coder:30b")
    assert engine.calls == [("signal", "coder-active"), ("load", "qwen3-coder:30b")]


def test_a_refused_downshift_stops_the_load(engine, enabled):
    """No confirmation, no load. 'Carry on anyway' is the over-budget load itself.

    A 404 is the realistic shape: ai-life's own flag is off, so the endpoint is not there.
    """
    engine.profile_ok = False
    with pytest.raises(LifecycleError):
        llm.generate("prompt", model="qwen3-coder:30b")
    assert engine.actions() == ["signal"]  # nothing was loaded
    assert not lifecycle.active()


def test_an_unreachable_gateway_stops_the_load(engine, enabled, monkeypatch):
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", refuse)
    with pytest.raises(LifecycleError):
        llm.generate("prompt", model="qwen3-coder:30b")
    assert not lifecycle.active()


def test_the_session_is_signalled_once_not_per_call(engine, enabled):
    """An enrich pass makes hundreds of calls; the handshake is per session, not per note."""
    for _ in range(3):
        llm.generate("prompt", model="qwen3-coder:30b")
    assert engine.actions().count("signal") == 1
    assert lifecycle.active()


def test_release_unloads_confirms_then_restores(engine, enabled):
    """The mirror ordering: our model must be gone from Ollama before ai-life is freed."""
    llm.generate("prompt", model="qwen3-coder:30b")
    engine.calls.clear()

    def drop(url, timeout=None):  # the model leaves on the first poll
        resp = engine.get(url, timeout)
        engine.resident.clear()
        return resp

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "get", drop)
        lifecycle.release("stop")

    assert engine.calls == [
        ("unload", "qwen3-coder:30b"),
        ("ps", ("qwen3-coder:30b",)),  # still resident right after the unload returns
        ("ps", ()),  # polled again — the unload is not the confirmation
        ("signal", "normal"),  # only once it is really gone
    ]
    assert not lifecycle.active()


def test_an_unconfirmed_eviction_never_signals_normal(engine, enabled, monkeypatch):
    """If our model will not leave, ai-life must NOT be told it may load its own on top."""
    monkeypatch.setattr(lifecycle.settings, "lifecycle_unload_timeout_s", 0)
    llm.generate("prompt", model="qwen3-coder:30b")
    engine.calls.clear()
    with pytest.raises(LifecycleError):
        lifecycle.release("stop")
    assert "signal" not in engine.actions()
    # The session is dropped anyway: the next call must redo the whole handshake rather than
    # assume it still holds an engine it failed to hand back.
    assert not lifecycle.active()


def test_a_failed_restore_is_logged_not_raised(engine, enabled):
    """Failing to restore ai-life leaves the box UNDER budget — a degradation, not a crash."""
    llm.generate("prompt", model="qwen3-coder:30b")
    engine.resident.clear()
    engine.restore_ok = False
    lifecycle.release("stop")  # must not raise
    assert not lifecycle.active()


def test_release_is_a_noop_when_nothing_was_acquired(engine, enabled):
    lifecycle.release("stop")
    assert engine.calls == []


def test_the_cloud_tier_never_signals(engine, enabled, monkeypatch):
    """An `anthropic:` model loads nothing on the shared Mac, so it must not make ai-life wait."""
    client = types.SimpleNamespace(
        messages=types.SimpleNamespace(
            create=lambda **kw: types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="A synthesized note.")]
            )
        )
    )
    monkeypatch.setattr(llm, "_cloud_client", lambda timeout_s: client)
    llm.generate("prompt", model="anthropic:claude-opus-4-8")
    assert engine.calls == []
    assert not lifecycle.active()


def test_idle_hands_the_engine_back_and_the_next_call_re_acquires(engine, enabled, monkeypatch):
    """A finished run must not hold ai-life on the small model until someone notices."""
    monkeypatch.setattr(lifecycle.settings, "lifecycle_idle_ttl_s", 900)
    llm.generate("prompt", model="qwen3-coder:30b")
    assert lifecycle._session.timer is not None  # the TTL is armed, not merely configured

    engine.resident.clear()
    lifecycle._on_idle()  # fired directly: a real 900 s wait is not a unit test
    assert not lifecycle.active()
    assert engine.calls[-1] == ("signal", "normal")

    llm.generate("prompt", model="qwen3-coder:30b")
    assert engine.actions().count("signal") == 3  # coder-active, normal, coder-active
