"""Golden lane — retrieval quality against a REAL Ollama + pgvector.

Opt-in (marked ``golden``, excluded from the default/CI run). Run it with a live dev DB + Ollama:
``uv run pytest -m golden`` or ``scripts/golden.sh``.

Structure-not-text assertions (like ai-life's golden tests): index a tiny fixture repo of clearly
distinct classes and assert the right one is retrieved / the right edges resolve. For determinism the
module works on a **clean slate** — it clears ``code.fragment`` and indexes only the fixture, so a
golden run leaves the dev DB holding just the fixture (the index is derived/rebuildable).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_context import db, tools
from code_context.indexer import index_repo

pytestmark = pytest.mark.golden

REPO = "golden-fixture"
FIXTURE = Path(__file__).parent / "fixtures" / "minirepo"


@pytest.fixture(scope="module", autouse=True)
def indexed():
    db.migrate()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE code.fragment RESTART IDENTITY CASCADE")
        conn.commit()
    stats = index_repo(str(FIXTURE), repo=REPO)
    assert stats["fragments"] > 0 and stats["edges"] > 0


def _top_classes(query: str, k: int = 3) -> list[str]:
    return [f["symbol"].split(".")[0] for f in tools.search_code(query, limit=k)]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("verify a user's login password", "PasswordHasher"),
        ("cosine similarity between two vectors", "VectorMath"),
        ("compute the total of an invoice", "InvoiceService"),
    ],
)
def test_retrieval_ranks_the_right_class(query, expected):
    tops = _top_classes(query, k=3)
    assert expected in tops, f"{query!r} → {tops}"


def test_get_deps_resolves_an_intra_repo_import():
    deps = {f["symbol"] for f in tools.get_deps("InvoiceService")}
    assert "LineItem" in deps  # java.util.List is external → correctly omitted


def test_find_usages_by_method_name():
    callers = {u["symbol"] for u in tools.find_usages("amount")}
    assert "InvoiceService.total" in callers
