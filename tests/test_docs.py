"""Unit tests for the docs HTML parser (C-3 / D-1).

The fixture is built to the `new-golden` rule: shapes that break parsers, not shapes that flatter
them — a skipped heading level, a page with no headings, a rule that exists only inside a table, a
section too large for one fragment, and injection-shaped prose.

Pure: no DB, no embeddings, no model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_context.indexer import docs

FIXTURES = Path(__file__).parent / "fixtures" / "docs"


def parse(name: str, **kw) -> list[docs.Section]:
    return docs.parse_html((FIXTURES / name).read_text(encoding="utf-8"), **kw)


def test_sections_carry_their_heading_path():
    sections = parse("payments-rules.html")
    paths = [s.heading_path for s in sections]
    assert ("Payments",) in paths
    assert ("Payments", "Refunds") in paths
    assert ("Payments", "Refunds", "Claim rules") in paths
    claim = next(s for s in sections if s.heading_path[-1:] == ("Claim rules",))
    assert claim.symbol == "Payments / Refunds / Claim rules"


def test_a_rule_living_only_in_a_table_survives():
    """In an exported wiki the table IS the rule — losing cells loses the content."""
    claim = next(s for s in parse("payments-rules.html") if s.heading_path[-1:] == ("Claim rules",))
    assert claim.has_table
    assert "Standard case" in claim.text
    assert "5 working days" in claim.text
    assert "DecisionServiceImpl" in claim.text  # the mention D-3 will link on


def test_code_blocks_are_kept_verbatim():
    examples = next(s for s in parse("payments-rules.html") if s.heading_path[-1:] == ("Examples",))
    assert "claimService.register(claim);" in examples.text


def test_code_block_indentation_survives_the_whitespace_pass():
    """Verbatim means the *shape* too — the whitespace pass is for prose, not for code.

    The section text goes through a whitespace normaliser that collapses runs of spaces and tabs,
    which is right for markup-exported prose and destructive for an example: a retrieved Java or
    YAML block with its indentation flattened is no longer the convention's example. The fixture
    was flat before this test, which is exactly why the defect shipped.
    """
    examples = next(s for s in parse("payments-rules.html") if s.heading_path[-1:] == ("Examples",))
    assert "    claimService.register(claim);" in examples.text
    assert "\n\n    DecisionServiceImpl" in examples.text  # a blank line inside the block survives


def test_prose_around_a_code_block_is_still_normalised():
    """Only the fenced span is held out — the paragraphs on either side still get cleaned."""
    (got,) = docs.parse_markdown(
        "spaced    prose\n\n```\n    indented\n```\n\nmore    prose\n"
    )
    assert "spaced prose" in got.text
    assert "more prose" in got.text
    assert "    indented" in got.text


def test_an_unterminated_fence_keeps_its_indentation():
    """A truncated example is still an example; falling back to cleaning would flatten it."""
    (got,) = docs.parse_markdown("```java\nif (x) {\n    call();\n")
    assert "    call();" in got.text


def test_a_skipped_heading_level_nests_by_containment():
    """h1 -> h3 with no h2: the numbering lies, the path must still say what contains what."""
    sections = parse("skipped-levels.html")
    deep = next(s for s in sections if s.heading_path[-1:] == ("Deeply nested rule",))
    assert deep.heading_path == ("Platform", "Deeply nested rule")
    assert deep.level == 3


def test_a_page_with_no_headings_still_yields_content():
    sections = parse("no-headings.html")
    assert len(sections) == 1
    assert sections[0].heading_path == ()
    assert sections[0].symbol == "(untitled)"
    assert "refunds are never automatic" in sections[0].text


def test_oversized_section_is_split_and_every_part_keeps_the_path():
    sections = parse("huge-section.html", max_chars=1000)
    assert len(sections) > 1
    assert all(s.heading_path == ("Huge",) for s in sections)
    assert all(len(s.text) <= 1000 for s in sections)
    assert [s.part for s in sections] == list(range(1, len(sections) + 1))
    assert sections[0].symbol.endswith(f"[1/{len(sections)}]")


def test_scripts_are_dropped_at_the_parse_boundary():
    text = " ".join(s.text for s in parse("injection.html"))
    assert "fetch(" not in text
    assert "evil.example" not in text


def test_injection_prose_is_carried_as_plain_content():
    """Not stripped, not obeyed — the parser only shapes text.

    Removing such prose would be security theatre (paraphrases slip through). The real boundary is
    that doc fragments are returned as data with provenance (D-4); here we only pin that the parser
    neither drops it silently nor treats it specially.
    """
    sections = parse("injection.html")
    text = " ".join(s.text for s in sections)
    assert "Ignore previous instructions" in text
    assert "Normal content continues here." in text


def test_no_empty_sections_are_emitted():
    for name in ("payments-rules.html", "skipped-levels.html", "injection.html"):
        assert all(s.text.strip() for s in parse(name))


def test_wrapper_divs_do_not_duplicate_content():
    """Exported markup nests wrapper divs; walking them naively repeats every paragraph."""
    sections = parse("payments-rules.html")
    intro = next(s for s in sections if s.heading_path == ("Payments",))
    assert intro.text.count("How payouts are handled") == 1


@pytest.mark.parametrize("html", ["", "<html></html>", "<html><body></body></html>"])
def test_empty_documents_yield_nothing(html):
    assert docs.parse_html(html) == []


def sections_by_heading(name: str) -> dict[str, docs.Section]:
    return {s.heading_path[-1]: s for s in parse(name) if s.heading_path}


# ── D-3: doc -> code linking (pure matching; the edge writing lives in the indexer) ──────────


def test_a_class_named_only_inside_a_table_is_still_matched():
    """The table IS the rule, so the class it names is the class the rule governs."""
    claim = sections_by_heading("payments-rules.html")["Claim rules"]
    assert docs.find_mentions(claim.text, {"DecisionServiceImpl", "ClaimRegistrationService"}) == [
        "ClaimRegistrationService",
        "DecisionServiceImpl",
    ]


def test_a_mention_inside_a_code_block_counts():
    """In a wiki the example is often the only place the real class name appears."""
    examples = sections_by_heading("payments-rules.html")["Examples"]
    assert docs.find_mentions(examples.text, {"DecisionServiceImpl"}) == ["DecisionServiceImpl"]


def test_matching_is_whole_token_not_substring():
    """`DecisionService` must not match inside `DecisionServiceImpl` — that is a wrong edge."""
    claim = sections_by_heading("payments-rules.html")["Claim rules"]
    assert docs.find_mentions(claim.text, {"DecisionService"}) == []


def test_matching_is_case_sensitive():
    """`claimService` (a variable in an example) is not the class `ClaimService`."""
    examples = sections_by_heading("payments-rules.html")["Examples"]
    assert docs.find_mentions(examples.text, {"ClaimService"}) == []


def test_a_section_naming_nothing_indexed_yields_no_links():
    text = " ".join(s.text for s in parse("injection.html"))
    assert docs.find_mentions(text, {"DecisionServiceImpl"}) == []
    assert docs.find_mentions(text, set()) == []


@pytest.mark.parametrize("symbol", ["DecisionServiceImpl", "ClaimService", "IOUtils", "RefundClaim"])
def test_multi_hump_class_names_are_linkable(symbol):
    assert docs.is_linkable(symbol)


@pytest.mark.parametrize("symbol", ["Claim", "Payment", "Status", "claimService", "", "SLA"])
def test_ordinary_domain_words_are_not_linkable(symbol):
    """A business wiki says "Claim" in prose constantly; an edge per occurrence is noise, not recall."""
    assert not docs.is_linkable(symbol)


def test_section_symbol_is_stable_for_incrementality():
    """The symbol is the fragment's identity — an unstable one would re-ingest everything."""
    a = docs.parse_html((FIXTURES / "payments-rules.html").read_text(encoding="utf-8"))
    b = docs.parse_html((FIXTURES / "payments-rules.html").read_text(encoding="utf-8"))
    assert [s.symbol for s in a] == [s.symbol for s in b]
    assert len(set(s.symbol for s in a)) == len(a)  # unique within a document
