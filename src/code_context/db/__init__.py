"""Database access for the code-context derived index (schema ``code.*``).

Schema changes are **migrations** (raw SQL under ``migrations/``, run by yoyo over our psycopg3) —
see ``migrations/README.md``. Open short-lived connections with :func:`connect`; apply pending
migrations with :func:`migrate`.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from ..config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def connect() -> psycopg.Connection:
    """Open a new connection to the configured Postgres (caller closes it)."""
    return psycopg.connect(settings.db_dsn)


def _yoyo_uri() -> str:
    """The DSN with the yoyo psycopg3 scheme (``postgresql+psycopg://``)."""
    dsn = settings.db_dsn
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+psycopg://" + dsn.removeprefix(prefix)
    return dsn


def migrate() -> list[str]:
    """Apply pending migrations (forward-only, ordered by filename). Returns the ids applied.

    Idempotent: already-applied migrations are skipped (yoyo tracks them in ``_yoyo_migration``).
    """
    from yoyo import get_backend, read_migrations

    backend = get_backend(_yoyo_uri())
    migrations = read_migrations(str(MIGRATIONS_DIR))
    with backend.lock():
        to_apply = backend.to_apply(migrations)
        backend.apply_migrations(to_apply)
        return [m.id for m in to_apply]
