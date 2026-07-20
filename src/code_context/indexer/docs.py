"""Docs ingest — parsing an exported HTML page into retrievable sections (roadmap C-3, slice D-1).

The docs counterpart of :mod:`code_context.indexer.java`: turn a source document into the units
retrieval returns. A **section** is the unit — a heading and the content beneath it — carrying its
**heading path** (``Payments / Refunds / Claim rules``), so a retrieved slice says where it came from.

Two things survive parsing on purpose:

- **tables**, because in an exported wiki a table often *is* the rule, not decoration;
- **code blocks**, verbatim, because they are usually the convention's example.

And one thing is removed on purpose: ``<script>``/``<style>`` and their content. Ingested pages are
**untrusted** (anyone can edit a wiki), so executable and presentational markup is dropped at the
parse boundary rather than carried downstream. Note that stripping markup is *not* a defence against
prose aimed at the agent — that is handled by treating doc fragments as data and tagging provenance
when they are returned (D-4). Nothing here interprets the text; it is only shaped.

**Two entries, one shape.** :func:`parse_html` handles an exported page; :func:`parse_markdown`
handles formats that reach us as markdown — a ``.docx`` through :func:`docx_to_markdown` (D-6) or
a ``.pdf`` through :func:`pdf_to_markdown` (D-7). Both drive the same section builder, so
containment behaves identically no matter what the source file was.

The two binary formats degrade differently, and both admit it rather than guessing: Word has no
structure beyond styles, so a document that fakes its headings yields one untitled section; a PDF
has no structure at all, so its tree is inferred from font size and uniform type likewise yields
one section.

Pure: no DB, no embeddings, no network. The ingest pass that stores these landed in D-2; the
symbol matching that links a section to the classes it governs (D-3) is here too — also pure, so
the precision rules are unit-testable without a database.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass

import mammoth
from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import markdownify

_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_DROP = ("script", "style", "noscript", "head")
# Block-level content we walk; anything else contributes its text through its parent.
_BLOCKS = ("p", "ul", "ol", "table", "pre", "blockquote", "div")
_WS = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")
# Java identifiers as whole tokens (D-3 linking): `.`, `<` and `(` are boundaries, so `Foo.bar`
# and `List<Claim>` both surface their parts, while `DecisionServiceImpl` never matches as
# `DecisionService`.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_HUMPS = re.compile(r"[A-Z][a-z0-9]*")
# Markdown structure (D-6). A fence may be ``` or ~~~ and may be indented up to 3 spaces; the
# closing run must be at least as long as the opening one, so a fence can quote a fence.
# The heading text is optional (CommonMark: a bare `#` is an empty heading), but the space after
# the hashes is not — otherwise `#hashtag` in prose would open a section.
_MD_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*#*[ \t]*$")
_MD_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
# PDF sectioning (D-7). A line must exceed the body size by this much to read as a heading —
# below it, ordinary typographic jitter (a superscript, a slightly larger capital) would open a
# section. Words within this many points vertically are the same line.
_PDF_HEADING_MARGIN = 1.0
# A size must carry at least this share of the most-used size's text to count as a body
# candidate. Below it, an occasional large pull-quote would be mistaken for the body.
_PDF_BODY_SHARE = 0.5
_PDF_LINE_TOLERANCE = 3.0


@dataclass(frozen=True)
class Section:
    """One retrievable slice of a document."""

    heading_path: tuple[str, ...]  # ("Payments", "Refunds", "Claim rules"); () for a headingless page
    level: int  # the heading's level (1-6); 0 when the page has no headings
    text: str
    has_table: bool = False
    part: int = 1  # >1 when an oversized section was split; the path is repeated on every part
    parts: int = 1

    @property
    def symbol(self) -> str:
        """The fragment symbol: the heading path, with a part suffix when the section was split."""
        base = " / ".join(self.heading_path) if self.heading_path else "(untitled)"
        return base if self.parts == 1 else f"{base} [{self.part}/{self.parts}]"


def parse_html(html: str, *, max_chars: int = 4000) -> list[Section]:
    """Parse an exported HTML page into sections, deepest heading context preserved.

    ``max_chars`` splits an oversized section rather than dropping content or emitting a fragment
    too large to embed usefully; every part keeps the same heading path.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_DROP)):
        tag.decompose()
    root = soup.body or soup

    builder = _Builder(max_chars)
    for el in _walk_blocks(root):
        if el.name in _HEADINGS:
            builder.heading(int(el.name[1]), _clean(el.get_text(" ")))
        elif el.name == "table":
            builder.block(_table_text(el), is_table=True)
        elif el.name == "pre":
            builder.block(f"```\n{el.get_text()}\n```")
        else:
            builder.block(_clean(el.get_text(" ")))
    return builder.done()


def parse_markdown(md: str, *, max_chars: int = 4000) -> list[Section]:
    """Parse markdown into the same sections a page would produce (roadmap C-3, slice D-6).

    The second entry to the section machinery, for formats that reach us as markdown rather than
    as markup — today a converted ``.docx``. ATX headings (``## Title``) define the tree, with the
    same containment rules as HTML: a skipped level nests rather than being renumbered.

    Fenced code survives verbatim, and a ``#`` **inside** a fence is content, not a heading — a
    convention page whose example is a shell script would otherwise shatter into a section per
    comment line. Pipe tables are kept as-is and mark the section ``has_table``, matching what
    :func:`parse_html` does with ``<table>``: in a wiki the table is often the rule itself.
    """
    builder = _Builder(max_chars)
    fence: str | None = None
    buf: list[str] = []

    def flush_para() -> None:
        text = "\n".join(buf).strip()
        if text:
            builder.block(text, is_table=any(_MD_TABLE_ROW.match(ln) for ln in text.splitlines()))
        buf.clear()

    for line in md.splitlines():
        opening = _MD_FENCE.match(line)
        if fence is not None:
            buf.append(line)
            if opening and opening.group(1)[0] == fence[0] and len(opening.group(1)) >= len(fence):
                fence = None
                flush_para()
            continue
        if opening:
            flush_para()
            fence = opening.group(1)
            buf.append(line)
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush_para()
            builder.heading(len(heading.group(1)), _clean(heading.group(2) or ""))
            continue
        if not line.strip():
            flush_para()
            continue
        buf.append(line)

    flush_para()  # an unterminated fence still yields its content rather than swallowing the tail
    return builder.done()


class _Builder:
    """Accumulates blocks under a heading stack — the shape both parsers produce.

    Shared on purpose: the containment rules (pop deeper-or-equal, a skipped level nests) are the
    part a reader relies on when a retrieved slice says where it came from, and two copies of them
    would drift apart the first time one parser was fixed.
    """

    def __init__(self, max_chars: int):
        self._max_chars = max_chars
        self._sections: list[Section] = []
        self._path: list[str] = []  # the current heading stack, one entry per level in play
        self._levels: list[int] = []
        self._buf: list[str] = []
        self._has_table = False

    def heading(self, level: int, title: str) -> None:
        self._flush()
        # Pop deeper-or-equal headings. A skipped level (h1 -> h3) simply nests: the path stays
        # truthful about containment even when the document's numbering is not.
        while self._levels and self._levels[-1] >= level:
            self._levels.pop()
            self._path.pop()
        self._levels.append(level)
        self._path.append(title)

    def block(self, text: str, *, is_table: bool = False) -> None:
        if is_table:
            self._has_table = True
        self._buf.append(text)

    def done(self) -> list[Section]:
        self._flush()
        return self._sections

    def _flush(self) -> None:
        text = _clean_outside_fences("\n\n".join(b for b in self._buf if b))
        if text:
            level = self._levels[-1] if self._levels else 0
            self._sections.extend(
                _split(
                    tuple(self._path),
                    level,
                    text,
                    has_table=self._has_table,
                    max_chars=self._max_chars,
                )
            )
        self._buf, self._has_table = [], False


def docx_to_markdown(data: bytes) -> str:
    """Convert a ``.docx`` to markdown (roadmap C-3, slice D-6).

    Two hops, both reused rather than written here: **mammoth** turns Word's style-based document
    into semantic HTML (``Heading 2`` → ``<h2>``, lists → ``<ul>``, tables → ``<table>``), then
    **markdownify** renders that as markdown. Word has no structure of its own beyond styles, so a
    document that fakes its headings with bold 16pt text has no tree to recover — it degrades to
    one untitled section, which is honest, rather than to a guess.

    Images are dropped: they carry no text to retrieve, and inlining them as base64 would bloat
    every fragment that followed. The markdown is what gets stored and indexed (Layer 1), so this
    conversion is the archived, diffable record of what the binary said.
    """
    html = mammoth.convert_to_html(io.BytesIO(data), convert_image=lambda _image: {}).value
    return markdownify(html, heading_style="ATX", bullets="-", strip=["img"]).strip()


def pdf_to_markdown(data: bytes) -> str:
    """Convert a text-layer ``.pdf`` to markdown (roadmap C-3, slice D-7).

    **A PDF has no structure — only glyphs at coordinates.** There is no heading element to read,
    so the tree is inferred: the most common font size on the page is the body text, and a line set
    noticeably larger is a heading whose level follows its rank among the larger sizes. That is a
    heuristic, and it is the honest limit of the format — a document typeset with one uniform size
    yields one untitled section rather than an invented tree.

    Tables are lifted out first (``find_tables``) and their glyphs excluded from the text pass, so
    a rule that lives in a table is rendered once as a pipe table instead of twice — once mangled
    into prose, once as a table.

    Returns ``""`` for a **scanned** document (images, no text layer). OCR is deliberately out of
    scope here: the caller reports the empty result rather than ingesting a blank fragment, which
    keeps "we cannot read this yet" distinguishable from "this page is empty".
    """
    import pdfplumber  # imported lazily: pulls pdfminer + pillow, and only .pdf corpora need it

    out: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            out.extend(_pdf_page_blocks(page))
    return _BLANKS.sub("\n\n", "\n\n".join(b for b in out if b.strip())).strip()


def _pdf_page_blocks(page) -> list[str]:
    """One page → markdown blocks, in reading order (tables and text interleaved by position)."""
    tables = page.find_tables()
    items: list[tuple[float, str]] = []
    for table in tables:
        rendered = _pdf_table(table.extract())
        if rendered:
            items.append((table.bbox[1], rendered))

    # Drop glyphs that belong to a table so they are not also read as prose.
    boxes = [t.bbox for t in tables]
    words = [
        w
        for w in page.extract_words(extra_attrs=["size"])
        if not any(x0 <= w["x0"] <= x1 and top <= w["top"] <= bottom for x0, top, x1, bottom in boxes)
    ]
    lines = _pdf_lines(words)
    if not lines:
        return [text for _, text in sorted(items)]

    # Body size = **the smallest size that carries a substantial share of the text**, weighed by
    # characters rather than by lines. Two weaker rules were tried and are wrong:
    #   * most lines — a short memo (one heading, one body line) is a tie the heading can win;
    #   * most characters — a long heading over a short paragraph outweighs it.
    # Both promote a heading size to "body" and then suppress *every* heading in the document. The
    # rule that holds is the typographic one: body text is the smallest size set in quantity, and
    # headings are larger and rarer.
    weighed = Counter()
    for _, size, text in lines:
        weighed[size] += len(text)
    substantial = max(weighed.values()) * _PDF_BODY_SHARE
    body_size = min(size for size, chars in weighed.items() if chars >= substantial)
    # Heading levels follow the *rank* of a size, not its absolute value: documents disagree about
    # what 14pt means, but they agree that bigger is higher up.
    larger = sorted({size for _, size, _ in lines if size > body_size + _PDF_HEADING_MARGIN},
                    reverse=True)
    for top, size, text in lines:
        if size in larger:
            items.append((top, f"{'#' * min(larger.index(size) + 1, 6)} {text}"))
        else:
            items.append((top, text))
    return [text for _, text in sorted(items)]


def _pdf_lines(words: list[dict]) -> list[tuple[float, float, str]]:
    """Group words into lines by their vertical position → ``(top, max size, text)``."""
    rows: dict[float, list[dict]] = {}
    for w in words:
        rows.setdefault(round(w["top"] / _PDF_LINE_TOLERANCE), []).append(w)
    lines = []
    for row in rows.values():
        row.sort(key=lambda w: w["x0"])
        text = _clean(" ".join(w["text"] for w in row))
        if text:
            lines.append((min(w["top"] for w in row), round(max(w["size"] for w in row), 1), text))
    return sorted(lines)


def _pdf_table(rows: list[list[str | None]]) -> str:
    """Render an extracted table as a pipe table — the same shape the HTML path produces."""
    body = [
        "| " + " | ".join(_clean(cell or "") for cell in row) + " |"
        for row in rows
        if any(cell and cell.strip() for cell in row)
    ]
    if not body:
        return ""
    width = len(rows[0])
    return "\n".join([body[0], "| " + " | ".join(["---"] * width) + " |", *body[1:]])


def _walk_blocks(root: Tag):
    """Yield headings and block elements in document order, without descending into a yielded block.

    A ``div`` is only yielded when it holds text directly — exported markup nests wrapper divs many
    deep, and yielding those would duplicate every paragraph inside them.
    """
    for child in root.children:
        if isinstance(child, NavigableString):
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in _HEADINGS or child.name in ("table", "pre", "p", "ul", "ol", "blockquote"):
            yield child
            continue
        if child.name in _BLOCKS or child.name in ("body", "html", "section", "article", "main"):
            if _has_direct_text(child):
                yield child
            else:
                yield from _walk_blocks(child)
        else:
            yield from _walk_blocks(child)


def _has_direct_text(tag: Tag) -> bool:
    """True when the tag holds text of its own, not only nested block elements."""
    return any(
        isinstance(c, NavigableString) and c.strip()
        for c in tag.children
    )


def _table_text(table: Tag) -> str:
    """Render a table as pipe-separated rows — the rule lives in the cells, so it has to survive."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [_clean(td.get_text(" ")) for td in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _clean(text: str) -> str:
    return _BLANKS.sub("\n\n", _WS.sub(" ", text)).strip()


def _clean_outside_fences(text: str) -> str:
    """Normalise prose whitespace, leaving fenced code **verbatim**.

    ``_clean`` exists to undo the whitespace an exported page's markup leaves behind, and for prose
    that is right. For a code block it is destructive: indentation *is* the content, so a flattened
    Java or YAML example is no longer the convention's example this module promises to preserve.
    Both parsers mark code with a fence — :func:`parse_html` wraps a ``<pre>`` in one, and markdown
    arrives fenced — so the fence is the one boundary this pass has to respect, and the scanner is
    the same one :func:`parse_markdown` uses (a closing run at least as long as the opening one, so
    a fence can quote a fence).

    An unterminated fence keeps its tail verbatim rather than falling back to cleaning: a truncated
    example is still an example, and guessing that the fence was really prose would flatten it.
    """
    parts: list[str] = []
    buf: list[str] = []
    fence: str | None = None

    for line in text.splitlines():
        opening = _MD_FENCE.match(line)
        if fence is None:
            if opening:
                parts.append(_clean("\n".join(buf)))
                buf, fence = [line], opening.group(1)
            else:
                buf.append(line)
            continue
        buf.append(line)
        if opening and opening.group(1)[0] == fence[0] and len(opening.group(1)) >= len(fence):
            parts.append("\n".join(buf))
            buf, fence = [], None

    tail = "\n".join(buf)
    parts.append(tail.strip("\n") if fence is not None else _clean(tail))
    return "\n\n".join(p for p in parts if p).strip()


def is_linkable(symbol: str) -> bool:
    """True when a class name is distinctive enough to link on from prose (D-3).

    Matching is by simple name — the same syntactic limitation as ``find_usages`` — so the only
    lever on precision is *which* names we are willing to match. A multi-hump CamelCase name
    (``DecisionServiceImpl``, ``ClaimRepository``) is a symbol wherever it appears in a wiki page.
    A single-word one (``Claim``, ``Payment``, ``Status``) is also an ordinary domain word, and a
    business page is full of those: linking on it would attach half the corpus to one class.
    High-recall linking on those names is worse than no link, because a wrong edge reads exactly
    like a right one. So we take the recall loss, and say so rather than overclaim.

    An all-caps name (``SLA``, ``KPI``) is rejected for the same reason: in this domain those are
    the wiki's vocabulary, not its symbols.
    """
    return (
        bool(symbol)
        and symbol[0].isupper()
        and any(c.islower() for c in symbol)
        and len(_CAMEL_HUMPS.findall(symbol)) >= 2
    )


def find_mentions(text: str, symbols: frozenset[str] | set[str]) -> list[str]:
    """The symbols from ``symbols`` that appear in ``text`` as whole identifiers, sorted.

    Whole-token matching only: ``DecisionService`` must not match inside ``DecisionServiceImpl``,
    and a name inside a code block counts the same as one in prose — in a wiki the example *is*
    often the reference. Nothing here interprets the text; a mention is an observation, not a claim
    that the page is about the class.
    """
    if not symbols:
        return []
    return sorted({t for t in _IDENT.findall(text) if t in symbols})


def _split(
    path: tuple[str, ...], level: int, text: str, *, has_table: bool, max_chars: int
) -> list[Section]:
    """One section, or several parts of an oversized one — split on paragraph boundaries."""
    if len(text) <= max_chars:
        return [Section(path, level, text, has_table=has_table)]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
        # A single paragraph larger than the budget: hard-split it rather than emit an over-budget
        # fragment (a wiki page with one giant table hits this).
        while len(current) > max_chars:
            chunks.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        chunks.append(current)
    return [
        Section(path, level, c, has_table=has_table, part=i, parts=len(chunks))
        for i, c in enumerate(chunks, start=1)
    ]
