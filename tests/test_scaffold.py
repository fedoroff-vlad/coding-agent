"""C-0 smoke tests: the scaffold is wired and its contracts are frozen.

No live Postgres / Ollama here — this asserts the MCP surface, config, the not-yet-implemented
stubs, and that the schema DDL resource is present. Behaviour tests arrive with C-1/C-2.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from code_context import db, server, tools
from code_context.config import settings

EXPECTED_TOOLS = {
    "search_code",
    "get_file",
    "find_usages",
    "get_deps",
    "find_convention",
    "search_docs",
}


def test_all_tools_register_over_mcp():
    registered = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert registered == EXPECTED_TOOLS


def test_no_tool_is_a_stub_anymore():
    """D-4 filled the last two (`find_convention` / `search_docs`); the surface is complete.

    Kept as an assertion rather than deleted: it is what tells a future slice that adding a tool
    means implementing it, not landing another `NotImplementedError` on the public surface.
    """
    for name in EXPECTED_TOOLS:
        source = inspect.getsource(getattr(tools, name))
        assert "NotImplementedError" not in source, f"{name} is still a stub"


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_every_tool_is_repo_scopable(name):
    """One index holds several projects; a tool that cannot be scoped mixes them (the D-4 defect)."""
    assert "repo" in inspect.signature(getattr(tools, name)).parameters
    assert "repo" in inspect.signature(getattr(server, name)).parameters


def test_config_defaults_are_consistent():
    # embed dim must match the vector(N) column so inserts don't fail at runtime.
    assert settings.embed_dim == 768
    assert settings.db_schema == "code"


def test_initial_migration_present():
    ddl = (db.MIGRATIONS_DIR / "0001_initial_schema.sql").read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS code" in ddl
    assert "code.fragment" in ddl
    assert "code.edge" in ddl
    assert f"vector({settings.embed_dim})" in ddl
