"""The lifecycle signal to ai-life (C-6a) — the coder half of a two-tenant Mac.

Both contours share one machine and one Ollama, and macOS caps the GPU working set at ~48 GB of
the 64. ai-life normally runs a 32B model; our analyzer is a 30B coder. **Both resident at once is
over budget**, so ai-life downshifts to a smaller model while we work — and the handshake ordering
is the opposite of the intuitive one (`../ai-life/plans/lifecycle.md` §B, slice LC-4):

1. **Start:** we signal ``coder-active`` **before loading anything** and wait for ai-life to
   *confirm* its big model is evicted. Only then do we load ours.
2. **Stop / idle:** we unload our model, *confirm* it is gone from Ollama, and only then signal
   ``normal`` so ai-life may restore its big one.

Loading first and signalling after is precisely the crash the downshift exists to prevent, on every
session start. Seamlessness is explicitly not required (owner) — the handshake simply waits.

**Opt-in, default OFF.** With the flag off nothing here makes a single HTTP call: this repo runs
standalone with no knowledge of ai-life, and ai-life runs standalone with its own flag off. Turning
it on is a statement that the two are co-resident on one box.

**A failed handshake fails loudly.** If ai-life will not confirm the downshift we raise instead of
loading the coder model, because "carry on anyway" is an over-budget load. The mirror case —
failing to restore ai-life's big model afterwards — is a degradation, not a crash, so it is logged
rather than raised.

Only the **analyzer** is gated. Embeddings (``nomic-embed-text``, a few hundred MB) do not move the
ceiling that this exists to defend, and gating them would make every ``index`` run wait on ai-life.
The cloud tier loads nothing locally, so it never signals either.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time

import httpx

from . import obs
from .config import settings

#: Profile names ai-life's ``/v1/model-profile`` understands (its LC-4 contract).
CODER_ACTIVE = "coder-active"
NORMAL = "normal"

_POLL_INTERVAL_S = 1.0


class LifecycleError(RuntimeError):
    """The handshake could not be completed — the coder model must not be loaded."""


class _Session:
    """What we hold on the shared engine right now. All access under :data:`_lock`."""

    def __init__(self) -> None:
        self.active = False
        self.models: set[str] = set()
        self.timer: threading.Timer | None = None


_lock = threading.RLock()
_session = _Session()
_atexit_registered = False


def acquire(model: str) -> None:
    """Make it safe to load ``model`` into the shared Ollama, and keep the session alive.

    Called immediately before every local analyzer call: that call is what loads the model, so
    this is the last moment at which the ordering guarantee can still be honoured. Cheap and
    idempotent once the session is up — it only restarts the idle timer.

    Raises :class:`LifecycleError` if ai-life does not confirm the downshift.
    """
    if not settings.lifecycle_enabled:
        return
    with _lock:
        if not _session.active:
            _signal(CODER_ACTIVE, settings.lifecycle_signal_timeout_s)
            _session.active = True
            _register_atexit()
        # Recorded before the call, not after: a call that times out half-way may still have
        # loaded the model, and an unreleased model is exactly what busts the budget.
        _session.models.add(model)
        _restart_idle_timer()


def release(reason: str = "stop") -> None:
    """Give the shared engine back: unload our models, confirm, then signal ``normal``.

    Idempotent and safe to call when nothing was ever acquired (the flag-off case is a no-op).
    Raises :class:`LifecycleError` if a model cannot be confirmed gone — ai-life is then left on
    the small model, which is the safe side of the failure.
    """
    with _lock:
        if not _session.active:
            return
        _cancel_idle_timer()
        models = sorted(_session.models)
        # Clear the session first: whatever happens below, the next analyzer call must redo the
        # full handshake rather than assume it still holds the engine.
        _session.active = False
        _session.models = set()

        deadline = time.monotonic() + settings.lifecycle_unload_timeout_s
        for model in models:
            _unload(model, deadline)

        restored = True
        try:
            _signal(NORMAL, settings.lifecycle_signal_timeout_s)
        except Exception:
            # Our model is gone, so the box is under budget; ai-life just stays downshifted until
            # something signals again. A degradation, not the over-budget load — hence no raise.
            restored = False
        obs.event(
            "lifecycle.release",
            logging.INFO if restored else logging.WARNING,
            "" if restored else "ai-life was not restored to its normal profile",
            reason=reason,
            models=len(models),
            restored=restored,
        )


def active() -> bool:
    """Whether we currently hold the shared engine (the signal was sent and not released)."""
    with _lock:
        return _session.active


def _signal(profile: str, timeout_s: int) -> None:
    """POST one profile switch and require a confirming response.

    ai-life's endpoint answers only once the outgoing model has actually left its engine, so a
    2xx *is* the confirmation. Any other outcome — a refusal, an unreachable gateway, or a 404
    because ai-life's own flag is off — means the downshift did not happen.
    """
    url = f"{settings.lifecycle_gateway_url.rstrip('/')}/v1/model-profile"
    try:
        with obs.timed("lifecycle.signal", profile=profile, url=url):
            resp = httpx.post(url, json={"profile": profile}, timeout=timeout_s)
            resp.raise_for_status()
    except Exception as exc:
        raise LifecycleError(
            f"ai-life did not confirm profile '{profile}' at {url}: {exc}. "
            "Refusing to proceed — see architecture.md §Contours "
            "(or set CODE_CONTEXT_LIFECYCLE_ENABLED=false to run standalone)."
        ) from exc


def _unload(model: str, deadline: float) -> None:
    """Evict one model from Ollama and wait until it is really gone.

    ``keep_alive: 0`` on an empty generate is Ollama's unload; it returns before the memory is
    actually released, so ``/api/ps`` is polled until the model disappears. Fire-and-forget here
    would hand ai-life a green light while ~19 GB was still resident.
    """
    base = settings.ollama_url.rstrip("/")
    with obs.timed("lifecycle.unload", model=model):
        httpx.post(
            f"{base}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=max(1.0, deadline - time.monotonic()),
        ).raise_for_status()
        while _is_resident(base, model):
            if time.monotonic() >= deadline:
                raise LifecycleError(
                    f"model '{model}' is still resident in Ollama after "
                    f"{settings.lifecycle_unload_timeout_s}s — not signalling '{NORMAL}', "
                    "because ai-life would then load its own model on top of ours."
                )
            time.sleep(_POLL_INTERVAL_S)


def _is_resident(base: str, model: str) -> bool:
    """Whether Ollama still reports ``model`` as loaded."""
    resp = httpx.get(f"{base}/api/ps", timeout=10)
    resp.raise_for_status()
    loaded = resp.json().get("models") or []
    return any(m.get("model") == model or m.get("name") == model for m in loaded)


def _restart_idle_timer() -> None:
    """(Re)arm the idle release. Caller holds the lock."""
    _cancel_idle_timer()
    ttl = settings.lifecycle_idle_ttl_s
    if ttl <= 0:
        return
    timer = threading.Timer(ttl, _on_idle)
    timer.daemon = True  # never keep the process alive just to hand the engine back
    _session.timer = timer
    timer.start()


def _cancel_idle_timer() -> None:
    if _session.timer is not None:
        _session.timer.cancel()
        _session.timer = None


def _on_idle() -> None:
    """The idle TTL expired — hand the engine back; the next analyzer call re-acquires."""
    try:
        release("idle")
    except Exception as exc:  # a raise in a timer thread would vanish silently
        obs.event("lifecycle.release", logging.ERROR, str(exc), reason="idle", restored=False)


def _register_atexit() -> None:
    """Backstop for any entry point that does not release explicitly. Caller holds the lock."""
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_on_exit)
        _atexit_registered = True


def _on_exit() -> None:
    try:
        release("exit")
    except Exception as exc:
        obs.event("lifecycle.release", logging.ERROR, str(exc), reason="exit", restored=False)


def _reset() -> None:
    """Drop all state without talking to anything (tests only)."""
    with _lock:
        _cancel_idle_timer()
        _session.active = False
        _session.models = set()
