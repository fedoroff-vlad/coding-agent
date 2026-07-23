"""CLI-wiring tests for `dev` subcommands — the thin argument plumbing, engines stubbed.

The commands themselves touch the DB / filesystem / an LLM (covered by golden lanes); here we assert
only that the CLI forwards its arguments correctly, which is where an off-by-one in `*args` bites.
"""

from __future__ import annotations

from code_context import dev, indexer


def test_enrich_cli_passes_subpath_through(monkeypatch):
    calls: dict[str, object] = {}

    def fake_enrich_repo(repo_path, subpath=None):
        calls["repo_path"] = repo_path
        calls["subpath"] = subpath
        return {"classes": 0, "trivial": 0, "noted": 0, "skipped": 0}

    monkeypatch.setattr(indexer, "enrich_repo", fake_enrich_repo)
    assert dev.enrich("/repo", "service/tof") == 0
    assert calls == {"repo_path": "/repo", "subpath": "service/tof"}


def test_enrich_cli_defaults_subpath_to_none(monkeypatch):
    calls: dict[str, object] = {}

    def fake_enrich_repo(repo_path, subpath=None):
        calls["subpath"] = subpath
        return {"classes": 0, "trivial": 0, "noted": 0, "skipped": 0}

    monkeypatch.setattr(indexer, "enrich_repo", fake_enrich_repo)
    assert dev.enrich("/repo") == 0  # the whole-repo form still works
    assert calls["subpath"] is None
