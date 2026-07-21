"""Observability contract tests (architecture.md §Observability).

The load-bearing one is :func:`test_no_payload_leaks_into_events`: events are destined for a
central, searchable, retained index, so customer source code must never reach them.
"""

from __future__ import annotations

import json
import logging

import pytest

from code_context import obs


@pytest.fixture
def events(monkeypatch):
    """Capture emitted events as parsed JSON objects."""
    logger = logging.getLogger("code_context")
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "level", logging.DEBUG)
    # `logging` caches the effective-level answer per logger, and only `setLevel` invalidates it.
    # Setting `.level` directly (so monkeypatch can restore it) skips that, so a cache populated by
    # any earlier test that emitted an event would silently swallow everything here — the fixture
    # would capture nothing and the assertions would fail with no hint why. Clear it both ways.
    logger._cache.clear()
    formatter = obs.JsonLinesFormatter()
    yield lambda: [json.loads(formatter.format(r)) for r in records]
    logger._cache.clear()


def test_event_carries_the_stable_field_set(events):
    obs.event("enrich.note", input="DubService.java", output="DubService.md")
    (e,) = events()
    assert e["event.action"] == "enrich.note"
    assert e["log.level"] == "info"
    assert e["run_id"] == obs.RUN_ID
    assert e["input"] == "DubService.java" and e["output"] == "DubService.md"
    assert e["@timestamp"].endswith("+00:00")  # UTC, ISO-8601


def test_timed_reports_duration_and_outcome(events):
    with obs.timed("llm.generate", model="qwen3:8b") as ev:
        ev["response_chars"] = 42
    (e,) = events()
    assert e["outcome"] == "ok"
    assert isinstance(e["event.duration"], int)
    assert e["response_chars"] == 42


def test_timed_marks_failure_and_reraises(events):
    with pytest.raises(ValueError), obs.timed("llm.generate"):
        raise ValueError("boom")
    (e,) = events()
    assert e["outcome"] == "error"
    assert e["log.level"] == "error"


def test_no_payload_leaks_into_events(events):
    """A prompt/note body must never be reconstructible from an event — sizes only."""
    prompt = "class Secret { void transferFunds(int amount) { ... } }"
    obs.check_context_pressure(len(prompt), num_ctx=8, model="qwen3:8b")  # tiny window -> warns
    with obs.timed("llm.generate", model="qwen3:8b", prompt_chars=len(prompt)) as ev:
        ev["response_chars"] = 10
    serialized = json.dumps(events())
    assert "transferFunds" not in serialized
    assert "Secret" not in serialized
    assert str(len(prompt)) in serialized  # the size is what we keep


def test_context_pressure_warns_only_when_crowded(events):
    obs.check_context_pressure(prompt_chars=100, num_ctx=8192, model="m")  # ~25 tokens: fine
    assert events() == []
    obs.check_context_pressure(prompt_chars=8192 * 4, num_ctx=8192, model="m")  # at the window
    (e,) = events()
    assert e["event.action"] == "llm.context_pressure"
    assert e["log.level"] == "warning"


def test_exception_is_collapsed_onto_one_line(events):
    """A multi-line traceback would be split by the shipper into unrelated documents."""
    logger = logging.getLogger("code_context")
    try:
        raise RuntimeError("read timed out")
    except RuntimeError:
        logger.error("", exc_info=True, extra={"action": "llm.generate", "fields": {}})
    (e,) = events()
    assert e["error.type"] == "RuntimeError"
    assert "\n" not in json.dumps(e)


def test_json_line_is_parseable_and_single_line():
    record = logging.LogRecord("code_context", logging.INFO, __file__, 1, "", None, None)
    record.action = "rollup.note"
    record.fields = {"input": "bo/claim", "children": 12}
    line = obs.JsonLinesFormatter().format(record)
    assert "\n" not in line
    assert json.loads(line)["children"] == 12
