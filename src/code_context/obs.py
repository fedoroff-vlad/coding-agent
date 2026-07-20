"""Observability — the one place that configures logging and emits events.

Contract (architecture.md §Observability): **JSON Lines on stderr, one self-contained event per
line, names and metrics only — never payloads.** Prompts, class bodies and note text carry customer
source code and these events are destined for a central searchable index, so there is deliberately
no switch that turns payload logging on.

stderr is a correctness constraint, not a preference: the MCP server owns stdout as its protocol
channel, and one stray line there corrupts the session.

Not named ``logging.py`` on purpose — a sibling module shadowing the stdlib name is a trap waiting
for the first ``import logging`` in this package.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from .config import settings

# One id per process: an enrich/rollup invocation groups as a single trace in Kibana.
RUN_ID = uuid.uuid4().hex[:12]

_LOGGER_NAME = "code_context"
# Keys the formatter renders itself — an event must not try to set them.
_RESERVED = frozenset({"@timestamp", "log.level", "event.action", "run_id", "message"})
# Rough chars-per-token for the context-pressure estimate. Deliberately crude: the point is to catch
# "this prompt is nowhere near / uncomfortably close to the window", not to count tokens exactly.
_CHARS_PER_TOKEN = 4
_CONTEXT_WARN_RATIO = 0.8


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line, ECS-aligned where that is free.

    Exceptions are collapsed onto the same line: a multi-line traceback would be split by the log
    shipper into unrelated documents.
    """

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, object] = {
            "@timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "log.level": record.levelname.lower(),
            "event.action": getattr(record, "action", record.name),
            "run_id": RUN_ID,
        }
        if record.getMessage():
            event["message"] = record.getMessage()
        for key, value in getattr(record, "fields", {}).items():
            if key not in _RESERVED:
                event[key] = value
        if record.exc_info:
            event["error.type"] = record.exc_info[0].__name__ if record.exc_info[0] else "unknown"
            # One line, no traceback: the type + message identify it; the payload rule keeps the
            # rest out anyway, since a traceback can quote source lines.
            event["error.message"] = str(record.exc_info[1])
        return json.dumps(event, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """The same events rendered for a human terminal. A rendering choice only — identical content."""

    def format(self, record: logging.LogRecord) -> str:
        action = getattr(record, "action", record.name)
        fields = " ".join(f"{k}={v}" for k, v in getattr(record, "fields", {}).items())
        parts = [f"{record.levelname.lower():5}", action, record.getMessage(), fields]
        return "  ".join(p for p in parts if p)


def setup() -> None:
    """Configure the package logger. Idempotent — safe to call from every entry point."""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:  # already configured (a second entry point in the same process)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonLinesFormatter() if settings.log_format == "json" else TextFormatter()
    )
    logger.addHandler(handler)
    logger.setLevel(settings.log_level.upper())
    logger.propagate = False  # never leak into a root handler that might target stdout


def event(action: str, level: int = logging.INFO, message: str = "", **fields: object) -> None:
    """Emit one event. ``fields`` must be names and metrics — never prompt/note/source text."""
    logging.getLogger(_LOGGER_NAME).log(
        level, message, extra={"action": action, "fields": fields}
    )


@contextmanager
def timed(action: str, level: int = logging.INFO, **fields: object):
    """Emit ``action`` with ``event.duration`` in ms, and an ``outcome`` of ok/error.

    Yields a dict — put result-derived fields (counts, output names) in it and they join the event.
    """
    started = time.perf_counter()
    extra: dict[str, object] = {}
    try:
        yield extra
    except Exception:
        event(
            action,
            logging.ERROR,
            **fields,
            **extra,
            outcome="error",
            **{"event.duration": round((time.perf_counter() - started) * 1000)},
        )
        raise
    event(
        action,
        level,
        **fields,
        **extra,
        outcome="ok",
        **{"event.duration": round((time.perf_counter() - started) * 1000)},
    )


def check_context_pressure(prompt_chars: int, num_ctx: int, model: str) -> None:
    """Warn when a prompt crowds the context window.

    Silent truncation raises nothing by construction — the engine just drops the overflow — so
    comparing the prompt against the configured window is the only way it becomes visible.
    """
    est_tokens = prompt_chars / _CHARS_PER_TOKEN
    if est_tokens >= num_ctx * _CONTEXT_WARN_RATIO:
        event(
            "llm.context_pressure",
            logging.WARNING,
            "prompt is close to the context window; output may be silently truncated",
            model=model,
            prompt_chars=prompt_chars,
            num_ctx=num_ctx,
            est_fill=round(est_tokens / num_ctx, 2),
        )
