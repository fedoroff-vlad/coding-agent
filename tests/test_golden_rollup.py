"""Golden lane — LLM note rollups against a REAL Ollama + pgvector (opt-in, not in unit CI).

Drives the full C-4b pass (index → enrich leaves → roll them up), so it proves the bottom-up
synthesis end to end: directory/module/project notes land as md + fragments and are retrievable.
Structure-not-text (model output is non-deterministic): assert the right *tiers* exist, the
marker dir becomes a `module`, and a high-level query retrieves a rollup — never exact wording.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from code_context import db, tools
from code_context.config import settings
from code_context.indexer import enrich_repo, index_repo, rollup_repo

pytestmark = pytest.mark.golden

REPO = "golden-rollup-fixture"
FIXTURE = Path(__file__).parent / "fixtures" / "minirepo"


@pytest.fixture(scope="module", autouse=True)
def rolled_up(tmp_path_factory):
    model = os.environ.get("CODE_CONTEXT_NOTES_MODEL", "qwen3:8b")
    settings.notes_model = model
    settings.rollup_model = os.environ.get("CODE_CONTEXT_ROLLUP_MODEL", model)
    settings.notes_root = str(tmp_path_factory.mktemp("notes"))
    db.migrate()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE code.fragment RESTART IDENTITY CASCADE")
        conn.commit()
    index_repo(str(FIXTURE), repo=REPO)
    enrich_repo(str(FIXTURE), repo=REPO)
    stats = rollup_repo(str(FIXTURE), repo=REPO)
    assert stats["rolled"] >= 1, stats
    return stats


def _rollups(kind: str) -> list[dict]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT path, symbol, content FROM code.fragment WHERE repo = %s AND kind = %s",
            (REPO, kind),
        )
        return [{"path": p, "symbol": s, "content": c} for p, s, c in cur.fetchall()]


def test_project_note_exists(rolled_up):
    projects = _rollups("project")
    assert len(projects) == 1 and projects[0]["content"].strip()  # the repo root, once


def test_billing_is_a_module(rolled_up):
    # billing/ carries a pom.xml marker → tagged kind='module' (not plain directory).
    modules = {r["path"] for r in _rollups("module")}
    assert "billing" in modules, modules


def test_plain_directories_get_directory_notes(rolled_up):
    dirs = {r["path"] for r in _rollups("directory")}
    assert {"auth", "math"} <= dirs, dirs  # non-marker code dirs


def test_a_rollup_is_retrievable(rolled_up):
    hits = tools.search_code("high-level overview of the whole codebase", limit=8)
    kinds = {h["kind"] for h in hits}
    assert kinds & {"directory", "module", "project"}, [h["kind"] for h in hits]


def test_rerun_is_incremental(rolled_up):
    # Nothing changed since the fixture ran → every tier skips, nothing re-rolled.
    again = rollup_repo(str(FIXTURE), repo=REPO)
    assert again["rolled"] == 0 and again["skipped"] >= 1, again
