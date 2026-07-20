"""Build a minimal ``.docx`` in memory, so the D-6 fixture is readable instead of a binary blob.

A ``.docx`` is a zip of XML parts. Committing one would make the fixture opaque — you could not
see *why* a parser test fails by reading the diff — so the tests construct exactly the document
they describe: a style-based heading tree, a table, and the shapes that break converters.
"""

from __future__ import annotations

import io
import zipfile

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def paragraph(text: str, style: str | None = None) -> str:
    """A paragraph, optionally carrying a Word style name (``Heading1``, ``Heading2``, …)."""
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{props}<w:r><w:t xml:space="preserve">{_escape(text)}</w:t></w:r></w:p>'


def bold_paragraph(text: str) -> str:
    """Bold body text — what a document that *fakes* its headings looks like structurally."""
    return (
        f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr>'
        f'<w:t xml:space="preserve">{_escape(text)}</w:t></w:r></w:p>'
    )


def table(rows: list[list[str]]) -> str:
    """A table — in a regulation document the table usually *is* the rule."""
    body = ""
    for row in rows:
        cells = "".join(
            f"<w:tc><w:tcPr/>{paragraph(cell)}</w:tc>" for cell in row
        )
        body += f"<w:tr>{cells}</w:tr>"
    return f"<w:tbl>{body}</w:tbl>"


def build_docx(*parts: str) -> bytes:
    """Zip the given body XML into a `.docx` mammoth can open."""
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document {_W}><w:body>'
        f'{"".join(parts)}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document)
    return buf.getvalue()
