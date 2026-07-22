"""The starter AGENTS.md (C-7's first slice).

Rendering is pure — the map is passed in — so everything here runs without a DB. What the tests
guard is not the prose but the two properties that make the file safe to hand to an agent: it tells
the shell to retrieve before reading, and it invents no conventions.
"""

from __future__ import annotations

import pytest

from code_context import agents_md


def test_render_names_the_tools_and_prefers_them():
    out = agents_md.render("my-repo", [])
    assert "# AGENTS.md — my-repo" in out
    # The whole reason the file exists: without an instruction to prefer them, a shell greps.
    for tool in ("search_code", "find_convention", "find_usages", "get_deps", "get_file"):
        assert tool in out


def test_render_states_no_convention_of_its_own():
    """Every rule must arrive as a TODO. A plausible invented convention is worse than none —
    it is authoritative-looking text the agent will follow."""
    out = agents_md.render("my-repo", [])
    todos = [ln for ln in out.splitlines() if ln.strip().startswith("- **")]
    assert todos, "the conventions section should list headings"
    assert "TODO" in out
    # Nothing may claim to know this repo's build.
    assert "mvn " not in out and "gradle " not in out


def test_render_maps_indexed_areas_with_counts():
    out = agents_md.render("my-repo", [("platform", 120), ("services", 40)])
    assert "`platform/` — 120 fragments" in out
    assert "`services/` — 40 fragments" in out
    assert "160 indexed fragments across 2 top-level areas" in out


def test_render_without_an_index_says_so_rather_than_pretending():
    out = agents_md.render("my-repo", [])
    assert "Not indexed yet" in out
    assert "fragments" not in out.split("## Conventions")[0].split("Map of the repository")[1]


def test_render_carries_the_untrusted_docs_rule():
    """Ingested wiki prose reaches the agent through these tools; the trust boundary has to be
    stated where the agent reads its instructions, not only in our own docs."""
    out = agents_md.render("my-repo", [])
    assert "not instructions" in out


def test_render_explains_that_nested_files_need_a_glob():
    """opencode does not auto-discover AGENTS.md in subdirectories — a monorepo hierarchy needs
    `instructions` in opencode.json. Nobody would guess that from a missing file."""
    out = agents_md.render("my-repo", [])
    assert "instructions" in out and "AGENTS.md" in out


def test_write_starter_uses_the_directory_name_as_the_repo_id(tmp_path, monkeypatch):
    monkeypatch.setattr(agents_md, "module_map", lambda repo, limit=40: [])
    target = tmp_path / "my-monorepo"
    target.mkdir()

    result = agents_md.write_starter(str(target))

    assert result["written"] is True
    assert (target / "AGENTS.md").read_text(encoding="utf-8").startswith("# AGENTS.md — my-monorepo")


def test_write_starter_refuses_to_clobber_an_authored_file(tmp_path, monkeypatch):
    """The file is Layer 1 — authored. Regenerating over it would drop the conventions a human
    wrote, which is the only part of it that carries knowledge."""
    monkeypatch.setattr(agents_md, "module_map", lambda repo, limit=40: [])
    target = tmp_path / "repo"
    target.mkdir()
    (target / "AGENTS.md").write_text("hand-written rules", encoding="utf-8")

    result = agents_md.write_starter(str(target))

    assert result["written"] is False
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "hand-written rules"

    result = agents_md.write_starter(str(target), force=True)
    assert result["written"] is True
    assert "hand-written rules" not in (target / "AGENTS.md").read_text(encoding="utf-8")


def test_write_starter_degrades_when_the_index_is_unreachable(tmp_path, monkeypatch):
    """The map is the optional half; the TODOs are the half that matters. A dead DB must not stop
    a repo from getting its authored layer."""
    def boom(repo, limit=40):
        raise RuntimeError("no database here")

    monkeypatch.setattr(agents_md, "module_map", boom)
    target = tmp_path / "repo"
    target.mkdir()

    result = agents_md.write_starter(str(target))

    assert result["written"] is True and result["modules"] == 0
    assert "Not indexed yet" in (target / "AGENTS.md").read_text(encoding="utf-8")


def test_write_starter_rejects_a_path_that_is_not_a_directory(tmp_path):
    f = tmp_path / "not-a-dir.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        agents_md.write_starter(str(f))
