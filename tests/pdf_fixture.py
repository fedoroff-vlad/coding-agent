"""Build a minimal PDF in memory, so the D-7 fixture is readable instead of a binary blob.

The point of the fixture is **font size**: a PDF has no headings, so sectioning is a size
heuristic, and a fixture that cannot set sizes per line cannot test it. Writing the file by hand
is what buys that control — and keeps the diff reviewable.

Each line is one text-showing operator at a given size; the writer emits a single-page document
per call, or several pages via :func:`build_pdf`.
"""

from __future__ import annotations


def line(text: str, size: float = 10.0) -> tuple[str, float]:
    """One line of text at a point size. Larger than the body size reads as a heading."""
    return (text, size)


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(lines: list[tuple[str, float]]) -> str:
    """Lay the lines out top-down on a 792pt-tall page, spaced by their own size."""
    ops = ["BT"]
    y = 752.0
    for text, size in lines:
        # Advance by the size of the line about to be drawn, not the one just drawn: a glyph box
        # extends *upward* from its baseline, so leading sized for a small preceding line lets a
        # large following one overlap it — and then the larger line's `top` is the smaller of the
        # two, which silently reverses reading order.
        y -= size * 1.6
        ops.append(f"/F1 {size} Tf")
        ops.append(f"1 0 0 1 72 {y:.1f} Tm")
        ops.append(f"({_escape(text)}) Tj")
    ops.append("ET")
    return "\n".join(ops)


def build_pdf(*pages: list[tuple[str, float]]) -> bytes:
    """Assemble a PDF with one content stream per page, and a correct xref table."""
    pages = pages or ([],)
    objects: list[bytes] = []

    def add(body: str | bytes) -> int:
        objects.append(body.encode("latin-1") if isinstance(body, str) else body)
        return len(objects)  # object numbers are 1-based

    font_id = 4 + 2 * len(pages)  # placed after the pages and their streams
    page_ids = [3 + 2 * i for i in range(len(pages))]
    add("<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    add(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    for i, lines in enumerate(pages):
        stream = _content_stream(lines)
        add(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {page_ids[i] + 1} 0 R >>"
        )
        add(
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode("latin-1")
        )
    # A bare standard-14 font. pdfminer logs a cosmetic "Could not get FontBBox" line for it;
    # metrics do not matter here, only the size each line was drawn at.
    add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def build_scanned_pdf() -> bytes:
    """A page with no text layer at all — what a scan looks like to a text extractor."""
    return build_pdf([])
