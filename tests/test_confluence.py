"""Confluence REST sync (roadmap decision B).

Driven against an ``httpx.MockTransport``, never a live wiki: the real one is behind a corporate
network, and the things worth pinning here — auth shape, pagination termination, incremental skip,
rename and deletion handling — are exactly the ones a live smoke test would *not* exercise on a
quiet space.
"""

from __future__ import annotations

import json

import httpx
import pytest

from code_context import confluence
from code_context.config import settings


@pytest.fixture(autouse=True)
def _wiki_settings(monkeypatch):
    monkeypatch.setattr(settings, "confluence_base_url", "https://wiki.example.internal")
    monkeypatch.setattr(settings, "confluence_api_path", "rest/api")
    monkeypatch.setattr(settings, "confluence_email", "")
    monkeypatch.setattr(settings, "confluence_body_format", "view")
    monkeypatch.setattr(settings, "confluence_page_limit", 2)
    monkeypatch.delenv(confluence.TOKEN_ENV, raising=False)


def _page(pid, title, body="<p>hi</p>", version=1):
    return {"id": pid, "title": title, "version": {"number": version}, "body": {"view": {"value": body}}}


def _client(batches):
    """A client answering /content with the given batches, one per request."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        payload = batches[min(len(calls) - 1, len(batches) - 1)]
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.calls = calls  # type: ignore[attr-defined]
    return client


# ── auth ───────────────────────────────────────────────────────────────────────────────

def test_data_center_token_is_sent_as_bearer():
    assert confluence.auth_headers("pat-123") == {"Authorization": "Bearer pat-123"}


def test_cloud_email_switches_to_basic():
    """Cloud wants email + API token as Basic. The email is the discriminator: a DC token presented
    with an empty Basic username is a 401 that reads like a bad token."""
    h = confluence.auth_headers("tok", "me@example.com")
    assert h["Authorization"].startswith("Basic ")
    import base64
    assert base64.b64decode(h["Authorization"].split()[1]).decode() == "me@example.com:tok"


def test_no_token_sends_no_header():
    assert confluence.auth_headers("") == {}


def test_api_root_refuses_to_run_without_a_base_url(monkeypatch):
    monkeypatch.setattr(settings, "confluence_base_url", "")
    with pytest.raises(RuntimeError, match="CONFLUENCE_BASE_URL"):
        confluence.api_root()


def test_api_root_joins_site_and_path_without_doubling_slashes(monkeypatch):
    monkeypatch.setattr(settings, "confluence_base_url", "https://site.atlassian.net/")
    monkeypatch.setattr(settings, "confluence_api_path", "/wiki/rest/api/")
    assert confluence.api_root() == "https://site.atlassian.net/wiki/rest/api"


# ── the page as the parser expects it ──────────────────────────────────────────────────

def test_page_html_puts_the_title_in_as_the_top_heading():
    """A REST body carries no title; an exported page does, and D-1 builds the section tree from
    headings. Without this every section hangs off whatever heading comes first."""
    out = confluence.page_html("Config conventions", "<h2>Retries</h2><p>x</p>")
    assert "<h1>Config conventions</h1>" in out
    assert out.index("<h1>") < out.index("<h2>")


def test_slug_leads_with_the_id_so_a_rename_keeps_one_file():
    assert confluence.slug("123", "Config Conventions!") == "123-config-conventions"
    assert confluence.slug("123", "面白い") == "123"   # unslugifiable title still yields a filename


# ── pagination ─────────────────────────────────────────────────────────────────────────

def test_short_page_ends_the_walk():
    client = _client([{"results": [_page("1", "A")], "_links": {}}])
    pages = list(confluence.fetch_pages("SPACE", client))
    assert [p[0] for p in pages] == ["1"]
    assert len(client.calls) == 1  # type: ignore[attr-defined]


def test_follows_next_across_batches():
    client = _client([
        {"results": [_page("1", "A"), _page("2", "B")], "_links": {"next": "/more"}},
        {"results": [_page("3", "C")], "_links": {}},
    ])
    assert [p[0] for p in confluence.fetch_pages("SPACE", client)] == ["1", "2", "3"]


def test_a_server_that_ignores_start_cannot_loop_forever():
    """A wiki repeating the same full batch would otherwise be an unbounded request loop against
    somebody's production Confluence."""
    client = _client([{"results": [_page("1", "A"), _page("2", "B")], "_links": {"next": "/more"}}])
    pages = list(confluence.fetch_pages("SPACE", client))
    assert [p[0] for p in pages] == ["1", "2"]
    assert len(client.calls) == 2  # type: ignore[attr-defined]


def test_body_format_selects_the_expansion(monkeypatch):
    monkeypatch.setattr(settings, "confluence_body_format", "storage")
    client = _client([{"results": [{"id": "1", "title": "A", "version": {"number": 1},
                                    "body": {"storage": {"value": "<p>raw</p>"}}}], "_links": {}}])
    (_, _, body, _), = confluence.fetch_pages("SPACE", client)
    assert body == "<p>raw</p>"
    assert "body.storage" in client.calls[0].url.params["expand"]  # type: ignore[attr-defined]


# ── sync to disk ───────────────────────────────────────────────────────────────────────

def test_sync_writes_one_file_per_page(tmp_path):
    client = _client([{"results": [_page("1", "Alpha"), _page("2", "Beta")], "_links": {}}])
    stats = confluence.sync_space("SPACE", str(tmp_path), client)

    assert stats == {"pages": 2, "written": 2, "skipped": 0, "removed": 0}
    assert (tmp_path / "1-alpha.html").exists() and (tmp_path / "2-beta.html").exists()
    assert "<h1>Alpha</h1>" in (tmp_path / "1-alpha.html").read_text(encoding="utf-8")


def test_second_sync_skips_unchanged_versions(tmp_path):
    batch = {"results": [_page("1", "Alpha", version=7)], "_links": {}}
    confluence.sync_space("SPACE", str(tmp_path), _client([batch]))
    stats = confluence.sync_space("SPACE", str(tmp_path), _client([batch]))
    assert stats["skipped"] == 1 and stats["written"] == 0


def test_a_bumped_version_is_refetched(tmp_path):
    confluence.sync_space("SPACE", str(tmp_path),
                          _client([{"results": [_page("1", "A", "<p>old</p>", 1)], "_links": {}}]))
    confluence.sync_space("SPACE", str(tmp_path),
                          _client([{"results": [_page("1", "A", "<p>new</p>", 2)], "_links": {}}]))
    assert "new" in (tmp_path / "1-a.html").read_text(encoding="utf-8")


def test_a_missing_local_file_is_refetched_even_at_the_same_version(tmp_path):
    """The manifest tracks upstream versions, not the disk. If someone cleans the corpus, a sync
    that trusted the manifest alone would leave the page permanently missing."""
    batch = {"results": [_page("1", "Alpha", version=3)], "_links": {}}
    confluence.sync_space("SPACE", str(tmp_path), _client([batch]))
    (tmp_path / "1-alpha.html").unlink()
    stats = confluence.sync_space("SPACE", str(tmp_path), _client([batch]))
    assert stats["written"] == 1 and (tmp_path / "1-alpha.html").exists()


def test_a_renamed_page_leaves_no_twin(tmp_path):
    confluence.sync_space("SPACE", str(tmp_path),
                          _client([{"results": [_page("1", "Old name", version=1)], "_links": {}}]))
    confluence.sync_space("SPACE", str(tmp_path),
                          _client([{"results": [_page("1", "New name", version=2)], "_links": {}}]))
    assert not (tmp_path / "1-old-name.html").exists()
    assert (tmp_path / "1-new-name.html").exists()


def test_a_deleted_page_is_removed_from_the_corpus(tmp_path):
    """Otherwise ingest_docs keeps re-embedding a page nobody can reach upstream any more."""
    confluence.sync_space("SPACE", str(tmp_path),
                          _client([{"results": [_page("1", "A"), _page("2", "B")], "_links": {}}]))
    stats = confluence.sync_space("SPACE", str(tmp_path),
                                  _client([{"results": [_page("1", "A")], "_links": {}}]))
    assert stats["removed"] == 1
    assert not (tmp_path / "2-b.html").exists()


def test_a_corrupt_manifest_forces_a_full_refetch_rather_than_failing(tmp_path):
    (tmp_path / confluence.MANIFEST).write_text("{not json", encoding="utf-8")
    stats = confluence.sync_space("SPACE", str(tmp_path),
                                  _client([{"results": [_page("1", "A")], "_links": {}}]))
    assert stats["written"] == 1
    assert json.loads((tmp_path / confluence.MANIFEST).read_text(encoding="utf-8")) == {"1": 1}
