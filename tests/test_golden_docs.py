"""Golden lane — the docs path end to end against a REAL Ollama + pgvector (C-3, D-4/D-5).

Opt-in (marked ``golden``, excluded from the default/CI run): ``scripts/golden.sh
tests/test_golden_docs.py``. No analyzer model is involved — docs ingest is parse → embed → link —
so this lane is cheap even on a CPU-only box, unlike the notes/rollup lanes.

What only a live run can prove, and why each assertion exists:

- the corpus is reachable by *meaning*, not by keyword (a real embedding model, real pgvector);
- a `mentions` edge survives the trip from HTML through the DB and comes back out of
  ``find_convention(symbol=...)`` — the D-3 join is what makes this more than a second search box;
- **the two corpora do not bleed into each other**: `search_code` must never return wiki prose and
  `search_docs` must never return code, and neither may cross a repo boundary. That last one is the
  defect this slice fixes, and it was only visible with two repos in one index.

Clean slate like the other lanes: the module truncates ``code.fragment`` and indexes only its
fixtures, so the dev DB is left holding just them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_context import db, tools
from code_context.indexer import index_repo, ingest_docs

pytestmark = pytest.mark.golden

REPO = "golden-fixture"
OTHER_REPO = "golden-other"  # a second project in the same index — the scoping guard
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def ingested():
    db.migrate()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE code.fragment RESTART IDENTITY CASCADE")
        conn.commit()
    index_repo(str(FIXTURES / "minirepo"), repo=REPO)
    # The same code and docs indexed a second time under another repo name: cheap, and it makes an
    # unscoped query provably wrong rather than merely suspicious.
    index_repo(str(FIXTURES / "minirepo"), repo=OTHER_REPO)
    stats = ingest_docs(str(FIXTURES / "docs-golden"), repo=REPO)
    assert stats["sections"] > 0 and stats["links"] > 0


def test_a_rule_is_retrievable_by_meaning_not_keyword():
    """The query shares no distinctive word with the section that answers it."""
    hits = tools.search_docs("what happens to a small unpaid debt", limit=3, repo=REPO)
    assert any("write-off threshold" in h["content"] for h in hits), [h["symbol"] for h in hits]


def test_every_doc_result_carries_its_provenance():
    for hit in tools.search_docs("invoice totals", limit=3, repo=REPO):
        assert hit["source"] == "docs"
        assert "not instructions" in hit["trust"]
        assert hit["document"].endswith(".html")
        assert hit["heading_path"]


def test_find_convention_puts_the_linked_section_first():
    """The graph answer — a page that names the class — outranks similarity."""
    hits = tools.find_convention("how are totals computed", symbol="InvoiceService", repo=REPO)
    assert hits, "no conventions returned"
    assert "InvoiceService" in hits[0]["mentions"]
    assert hits[0]["heading_path"].endswith("Invoice totals")


def test_find_convention_without_a_symbol_still_answers_semantically():
    hits = tools.find_convention("where are passwords hashed", repo=REPO)
    assert any("Credential storage" in h["heading_path"] for h in hits), [h["symbol"] for h in hits]


def test_find_convention_deduplicates_the_two_sources():
    hits = tools.find_convention("invoice totals rounding", symbol="InvoiceService", repo=REPO)
    keys = [(h["document"], h["symbol"]) for h in hits]
    assert len(keys) == len(set(keys))


def test_injection_shaped_prose_comes_back_as_tagged_content():
    """Carried, not obeyed and not silently dropped — the boundary is the tag, not a filter."""
    hits = tools.search_docs("notice board instructions", limit=5, repo=REPO)
    notice = next(h for h in hits if "Ignore previous instructions" in h["content"])
    assert notice["source"] == "docs" and "untrusted" in notice["trust"]


def test_the_two_corpora_do_not_bleed_into_each_other():
    code = tools.search_code("how are invoice totals computed", limit=5, repo=REPO)
    assert all(f["kind"] != "doc" for f in code), [f["kind"] for f in code]
    docs = tools.search_docs("invoice totals", limit=5, repo=REPO)
    assert all(d["document"].endswith(".html") for d in docs)


def test_retrieval_is_scoped_to_one_repo():
    """The defect this slice closes: two projects in one index used to share a result set."""
    assert all(f["repo"] == REPO for f in tools.search_code("password hashing", repo=REPO))
    assert all(f["repo"] == OTHER_REPO for f in tools.search_code("password hashing",
                                                                  repo=OTHER_REPO))
    # The docs were ingested under REPO only, so the other project's scope must come back empty
    # rather than borrow them.
    assert tools.search_docs("write-off threshold", repo=OTHER_REPO) == []
    assert tools.find_convention("totals", symbol="InvoiceService", repo=OTHER_REPO) == []


def test_usages_and_deps_are_scoped_too():
    assert tools.find_usages("amount", repo=REPO), "the fixture's call edge is missing"
    assert {f["repo"] for f in tools.get_deps("InvoiceService", repo=OTHER_REPO)} == {OTHER_REPO}
