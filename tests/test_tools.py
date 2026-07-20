"""Unit tests for the tool layer's pure parts (C-3 / D-4).

The queries themselves need a live DB and are covered by the golden lanes. What is unit-testable —
and what actually broke — is the *scoping* decision: every tool used to query ``code.fragment``
with no repo filter, so a docs corpus and a real project came back interleaved. That decision now
lives in one helper, and this pins its three cases.
"""

from __future__ import annotations

import pytest

from code_context import tools
from code_context.config import settings


@pytest.fixture
def no_default(monkeypatch):
    monkeypatch.setattr(settings, "default_repo", "")


@pytest.fixture
def with_default(monkeypatch):
    monkeypatch.setattr(settings, "default_repo", "configured-repo")


def test_an_explicit_repo_wins(with_default):
    assert tools._scope("asked-for") == "asked-for"


def test_the_configured_default_applies_when_the_caller_passes_none(with_default):
    assert tools._scope(None) == "configured-repo"


def test_no_argument_and_no_default_searches_everything(no_default):
    """Legitimate for a deliberate cross-repo index — and the only case that may go unscoped."""
    assert tools._scope(None) is None


def test_the_clause_is_empty_exactly_when_unscoped(no_default):
    assert tools._repo_clause(None) == ("", [])


def test_the_clause_is_parameterised_not_interpolated(no_default):
    """A repo name reaches us from a caller; it belongs in a bound parameter, never in the SQL."""
    clause, params = tools._repo_clause("my-repo")
    assert clause == " AND repo = %s"
    assert params == ["my-repo"]
    assert "my-repo" not in clause


def test_the_clause_qualifies_the_column_when_the_query_joins(no_default):
    """Unqualified `repo` is ambiguous in the joined queries — get_deps/find_usages need the alias."""
    clause, params = tools._repo_clause("my-repo", alias="f")
    assert clause == " AND f.repo = %s"
    assert params == ["my-repo"]
