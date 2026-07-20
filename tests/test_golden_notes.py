"""Golden lane — LLM leaf notes against a REAL Ollama + pgvector (opt-in, not in unit CI).

The generative counterpart of ``test_golden_retrieval``: it drives the actual analyzer model, so it
proves the C-4 enrich pass end to end (parser facts → LLM note → md on disk → note fragment →
retrievable), not just the pure gate/prompt logic that unit CI covers.

Structure-not-text (the model output is non-deterministic): assert the *right classes* get notes,
data carriers don't, notes land as md + ``note`` fragments, and a purpose query retrieves one — never
assert exact wording. Notes are redirected to a tmp tree so the fixture repo stays clean.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from code_context import db, tools
from code_context.config import settings
from code_context.indexer import enrich_repo, index_repo

pytestmark = pytest.mark.golden

REPO = "golden-notes-fixture"
FIXTURE = Path(__file__).parent / "fixtures" / "minirepo"


@pytest.fixture(scope="module", autouse=True)
def enriched(tmp_path_factory):
    # Dev box has no qwen3-coder:30b — use the small local model unless the env says otherwise.
    settings.notes_model = os.environ.get("CODE_CONTEXT_NOTES_MODEL", "qwen3:8b")
    settings.notes_root = str(tmp_path_factory.mktemp("notes"))
    db.migrate()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE code.fragment RESTART IDENTITY CASCADE")
        conn.commit()
    index_repo(str(FIXTURE), repo=REPO)
    stats = enrich_repo(str(FIXTURE), repo=REPO)
    assert stats["noted"] >= 1, stats
    return stats


def _notes_for(symbol: str) -> list[dict]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, content FROM code.fragment "
            "WHERE repo = %s AND kind = 'note' AND symbol = %s",
            (REPO, symbol),
        )
        return [{"symbol": s, "content": c} for s, c in cur.fetchall()]


def test_substantive_classes_get_a_note(enriched):
    # InvoiceService / PasswordHasher / VectorMath carry behavior → each earns a leaf note.
    for symbol in ("InvoiceService", "PasswordHasher", "VectorMath"):
        rows = _notes_for(symbol)
        assert rows and rows[0]["content"].strip(), f"no note for {symbol}"


def test_data_carrier_is_skipped(enriched):
    assert _notes_for("LineItem") == []  # a DTO stops at parser facts, no LLM note


def test_note_md_written_to_disk(enriched):
    root = Path(settings.notes_root)
    assert list(root.rglob("InvoiceService.md")), "expected an md note artifact on disk (Layer 1)"


def test_a_note_is_retrievable(enriched):
    hits = tools.search_code("what computes an invoice total", limit=8)
    assert any(h["kind"] == "note" for h in hits), [h["kind"] for h in hits]
