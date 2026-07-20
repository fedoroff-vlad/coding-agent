"""`.docx` → markdown → sections (roadmap C-3, slice D-6).

Built to the `new-golden` rule: the documents here are the shapes that break converters — a
document that fakes its headings, a rule that only exists in a table, a `#` inside a code fence,
a renamed file that is not a zip at all. A fixture of well-formed prose would pass against a
parser that did nothing.
"""

from __future__ import annotations

import pytest

from code_context import indexer
from code_context.indexer import docs

from docx_fixture import bold_paragraph, build_docx, paragraph, table


def sections_of(*parts: str, max_chars: int = 4000):
    return docs.parse_markdown(docs.docx_to_markdown(build_docx(*parts)), max_chars=max_chars)


def test_word_styles_become_the_heading_path():
    got = sections_of(
        paragraph("Claim rules", "Heading1"),
        paragraph("Payouts are approved by DecisionServiceImpl.", None),
        paragraph("Limits", "Heading2"),
        paragraph("The ceiling is 400000.", None),
    )
    assert [s.symbol for s in got] == ["Claim rules", "Claim rules / Limits"]
    assert got[1].level == 2


def test_a_rule_living_only_in_a_table_survives_the_conversion():
    (got,) = sections_of(
        paragraph("Limits", "Heading1"),
        table([["Case", "Limit"], ["Refund", "400000"]]),
    )
    assert got.has_table
    assert "Refund" in got.text and "400000" in got.text


def test_a_class_named_in_a_converted_doc_is_still_linkable():
    # The whole point of ingesting the document: D-3 has to be able to link it to the code.
    (got,) = sections_of(paragraph("DecisionServiceImpl owns the payout decision.", None))
    assert docs.find_mentions(got.text, {"DecisionServiceImpl"}) == ["DecisionServiceImpl"]


def test_a_document_that_fakes_its_headings_degrades_to_one_section():
    # Bold 16pt text is not a heading — Word has no structure beyond styles, so there is no tree
    # to recover. One untitled section is honest; inventing a heading from font size is a guess.
    got = sections_of(
        bold_paragraph("Looks Like A Heading"),
        paragraph("Body text underneath it.", None),
    )
    assert len(got) == 1
    assert got[0].symbol == "(untitled)"
    assert "Looks Like A Heading" in got[0].text  # content is kept, only the structure is absent


def test_an_empty_document_yields_nothing():
    assert sections_of(paragraph("   ", None)) == []


def test_a_file_that_is_not_a_zip_raises_rather_than_returning_garbage():
    # A `.doc` renamed to `.docx` is the common case. The ingest pass turns this into a warning
    # and keeps going; what must not happen is a silent empty fragment.
    with pytest.raises(Exception):  # noqa: B017 - the reader's own error type is not our contract
        docs.docx_to_markdown(b"\xd0\xcf\x11\xe0 not a zip")


# ── the markdown parser itself (also the seam the .pdf slice will reuse) ────────────────


def test_a_hash_inside_a_code_fence_is_content_not_a_heading():
    md = "# Real heading\n\n```bash\n# not a heading\necho hi\n```\n"
    got = docs.parse_markdown(md)
    assert [s.symbol for s in got] == ["Real heading"]
    assert "# not a heading" in got[0].text


def test_a_fence_can_quote_a_fence():
    md = "# Doc\n\n````\n```\ninner\n```\n````\n"
    got = docs.parse_markdown(md)
    assert [s.symbol for s in got] == ["Doc"]
    assert "inner" in got[0].text


def test_an_unterminated_fence_still_yields_its_content():
    got = docs.parse_markdown("# Doc\n\n```\ndangling\n")
    assert "dangling" in got[0].text


def test_a_skipped_level_nests_by_containment():
    got = docs.parse_markdown("# A\n\ntext\n\n### C\n\nmore\n")
    assert [s.symbol for s in got] == ["A", "A / C"]


def test_closing_hashes_are_not_part_of_the_title():
    (got,) = docs.parse_markdown("## Title ##\n\nbody\n")
    assert got.symbol == "Title"


def test_oversized_section_is_split_and_every_part_keeps_the_path():
    got = docs.parse_markdown("# Big\n\n" + "\n\n".join(["para"] * 400), max_chars=200)
    assert len(got) > 1
    assert {s.heading_path for s in got} == {("Big",)}
    assert [s.part for s in got] == list(range(1, len(got) + 1))
    assert all(len(s.text) <= 200 for s in got)


@pytest.mark.parametrize("md", ["", "   \n\n  \n", "#\n"])
def test_empty_markdown_yields_nothing(md):
    assert docs.parse_markdown(md) == []


def test_html_and_markdown_agree_on_containment():
    # The two entries share one builder precisely so these cannot drift apart.
    html = docs.parse_html("<h1>A</h1><p>one</p><h2>B</h2><p>two</p>")
    md = docs.parse_markdown("# A\n\none\n\n## B\n\ntwo\n")
    assert [(s.symbol, s.level, s.text) for s in html] == [(s.symbol, s.level, s.text) for s in md]


# ── the ingest-side rules (DB-free: file selection + the Layer-1 write) ─────────────────


def test_converted_markdown_is_archived_next_to_the_corpus(tmp_path):
    (tmp_path / "rules").mkdir()
    src = tmp_path / "rules" / "claims.docx"
    src.write_bytes(build_docx(paragraph("Claim rules", "Heading1"), paragraph("Body.", None)))

    got = indexer._parse_document(src, tmp_path)

    assert [s.symbol for s in got] == ["Claim rules"]
    archived = tmp_path / ".code-context/md/rules/claims.md"
    assert archived.read_text(encoding="utf-8").startswith("# Claim rules")


def test_an_unreadable_document_is_reported_and_skipped_not_fatal(tmp_path):
    bad = tmp_path / "renamed.docx"
    bad.write_bytes(b"\xd0\xcf\x11\xe0 actually an old .doc")
    # None, not an exception: one bad file in a corpus must not abort the whole ingest.
    assert indexer._parse_document(bad, tmp_path) is None


def test_a_docx_with_no_text_is_skipped_rather_than_ingested_empty(tmp_path):
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_docx(paragraph("   ", None)))
    assert indexer._parse_document(empty, tmp_path) is None


def test_word_lock_files_and_our_own_output_are_not_ingested(tmp_path):
    (tmp_path / ".code-context/md").mkdir(parents=True)
    (tmp_path / "page.html").write_text("<h1>x</h1>", encoding="utf-8")
    (tmp_path / "rules.docx").write_bytes(b"")
    (tmp_path / "~$rules.docx").write_bytes(b"")  # Word's lock file for the open document
    (tmp_path / ".code-context/md/rules.docx").write_bytes(b"")  # never re-ingest our own output
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")  # not a document format

    assert [p.name for p in indexer._document_files(tmp_path)] == ["page.html", "rules.docx"]
