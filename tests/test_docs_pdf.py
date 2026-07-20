"""`.pdf` → markdown → sections (roadmap C-3, slice D-7).

A PDF has no structure, only glyphs at coordinates, so the section tree is a **font-size
heuristic**. The fixture exists to pin that heuristic where it is honest and where it gives up:
uniform type yields no tree, a table is rendered once rather than twice, and a scan (no text
layer) is reported rather than ingested blank.
"""

from __future__ import annotations

from code_context import indexer
from code_context.indexer import docs

from pdf_fixture import build_pdf, build_scanned_pdf, line


def sections_of(*pages, max_chars: int = 4000):
    return docs.parse_markdown(docs.pdf_to_markdown(build_pdf(*pages)), max_chars=max_chars)


def test_font_size_becomes_the_heading_path():
    got = sections_of(
        [
            line("Claim rules", 20),
            line("Payouts are approved by DecisionServiceImpl.", 10),
            line("Limits", 14),
            line("The ceiling is 400000.", 10),
        ]
    )
    assert [s.symbol for s in got] == ["Claim rules", "Claim rules / Limits"]
    assert got[1].level == 2


def test_heading_level_follows_rank_not_absolute_size():
    # 40/28pt in one document and 14/12pt in another must both read as h1/h2: documents disagree
    # about what a point size means, but agree that bigger is higher up.
    big = sections_of([line("A", 40), line("body", 9), line("B", 28), line("body", 9)])
    small = sections_of([line("A", 14), line("body", 9), line("B", 12), line("body", 9)])
    assert [(s.symbol, s.level) for s in big] == [(s.symbol, s.level) for s in small]


def test_uniform_type_yields_one_untitled_section():
    # No size signal means no tree to recover. Inventing headings here would be a guess.
    got = sections_of([line("All of this", 10), line("is the same size", 10)])
    assert len(got) == 1
    assert got[0].symbol == "(untitled)"
    assert "All of this" in got[0].text


def test_slightly_larger_type_is_not_a_heading():
    # Typographic jitter — a marginally larger line — must not open a section.
    got = sections_of([line("Body one", 10), line("Body two", 10.4), line("Body three", 10)])
    assert len(got) == 1
    assert got[0].heading_path == ()


def test_a_class_named_in_a_pdf_is_still_linkable():
    (got,) = sections_of([line("DecisionServiceImpl owns the payout decision.", 10)])
    assert docs.find_mentions(got.text, {"DecisionServiceImpl"}) == ["DecisionServiceImpl"]


def test_pages_are_concatenated_in_order():
    got = sections_of(
        [line("Part one", 20), line("first page body", 10)],
        [line("Part two", 20), line("second page body", 10)],
    )
    assert [s.symbol for s in got] == ["Part one", "Part two"]


def test_a_scan_yields_no_text_rather_than_a_blank_fragment():
    # The whole reason OCR is out of scope: "cannot read yet" must stay distinguishable from
    # "read it, it was empty".
    assert docs.pdf_to_markdown(build_scanned_pdf()) == ""


def test_a_scanned_pdf_is_skipped_by_the_ingest_pass(tmp_path):
    scan = tmp_path / "scanned.pdf"
    scan.write_bytes(build_scanned_pdf())
    assert indexer._parse_document(scan, tmp_path) is None
    # Nothing is archived for a document we could not read.
    assert not (tmp_path / ".code-context/md/scanned.md").exists()


def test_a_file_that_is_not_a_pdf_is_skipped_not_fatal(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.4 truncated right here")
    assert indexer._parse_document(bad, tmp_path) is None


def test_converted_pdf_markdown_is_archived(tmp_path):
    src = tmp_path / "rules.pdf"
    src.write_bytes(build_pdf([line("Claim rules", 20), line("Body text.", 10)]))

    got = indexer._parse_document(src, tmp_path)

    assert [s.symbol for s in got] == ["Claim rules"]
    assert (tmp_path / ".code-context/md/rules.md").read_text(encoding="utf-8").startswith(
        "# Claim rules"
    )


def test_pdf_is_a_recognised_document_format(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"")
    assert [p.name for p in indexer._document_files(tmp_path)] == ["a.pdf"]
