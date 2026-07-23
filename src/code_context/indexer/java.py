"""Java chunker — tree-sitter parser facts (no LLM).

Walks a Java source file and yields the retrieval units: type declarations (class/interface/enum/
record) as a header fragment, and methods/constructors as full-body fragments. Names are exact
(from the grammar), so there's no hallucination. This is the "parser facts" layer of roadmap C-2;
LLM notes on top come later.
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter_language_pack import get_parser

_PARSER = get_parser("java")

# grammar node type -> our fragment kind
_TYPE_KINDS = {
    "class_declaration": "class",
    "interface_declaration": "class",
    "enum_declaration": "class",
    "record_declaration": "class",
}
_METHOD_KINDS = {
    "method_declaration": "method",
    "constructor_declaration": "method",
}


@dataclass
class FragmentData:
    """One parsed fragment. Offsets are 1-based, inclusive, matching editor line numbers."""

    kind: str  # class | method
    symbol: str  # simple name for a type; Type.method for a method
    signature: str
    line_start: int
    line_end: int
    content: str  # what gets embedded: the header for a type, the full text for a method


@dataclass(frozen=True)
class EdgeData:
    """A directed relation between symbols, from parser facts (syntactic — no type resolution).

    ``src_symbol`` is always a fragment in this file (a type or ``Type.method``). ``dst_symbol`` is a
    method name (calls), a fully-qualified import (imports), or a ``Type.method`` (contains); it may not
    resolve to an indexed fragment — resolution happens at query time / with the Java sidecar later.
    """

    src_symbol: str
    dst_symbol: str
    kind: str  # calls | imports | contains


def parse_source(source: str) -> list[FragmentData]:
    """Parse Java source text into fragments. Pure — no DB, no embeddings, no I/O."""
    src = source.encode("utf-8")
    out: list[FragmentData] = []
    _walk(_PARSER.parse(src).root_node, src, enclosing=None, out=out)
    return out


def _walk(node, src: bytes, enclosing: str | None, out: list[FragmentData]) -> None:
    qualifier = enclosing
    if node.type in _TYPE_KINDS:
        name = _name(node, src)
        if name:
            out.append(_fragment(node, src, _TYPE_KINDS[node.type], name, body_only=False))
            qualifier = name  # methods inside are qualified by this type
    elif node.type in _METHOD_KINDS:
        name = _name(node, src)
        if name:
            symbol = f"{enclosing}.{name}" if enclosing else name
            out.append(_fragment(node, src, "method", symbol, body_only=True))
    for child in node.children:
        _walk(child, src, qualifier, out)


def _name(node, src: bytes) -> str | None:
    field = node.child_by_field_name("name")
    return src[field.start_byte : field.end_byte].decode("utf-8", "replace") if field else None


def _fragment(node, src: bytes, kind: str, symbol: str, *, body_only: bool) -> FragmentData:
    signature = _signature(node, src)
    text = src[node.start_byte : node.end_byte].decode("utf-8", "replace")
    return FragmentData(
        kind=kind,
        symbol=symbol,
        signature=signature,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        content=text if body_only else signature,
    )


def _signature(node, src: bytes) -> str:
    """The declaration header — everything up to the body block, whitespace-collapsed."""
    body = node.child_by_field_name("body")
    end = body.start_byte if body else node.end_byte
    header = src[node.start_byte : end].decode("utf-8", "replace")
    return " ".join(header.split())


def class_fields(source: str) -> dict[str, list[str]]:
    """Field declarations per type: simple type name → its collapsed field declaration texts.

    A structural fact (declared state), not a fragment — it is not embedded or stored. Enrich folds
    it into the note prompt in *bodies mode* (trusted repo), so a note can name the state a class
    holds, not just its methods. Keyed by the simple type name, exactly like the type fragments'
    symbols, so it lines up with :func:`code_context.indexer.notes.class_units`.
    """
    src = source.encode("utf-8")
    out: dict[str, list[str]] = {}
    _walk_fields(_PARSER.parse(src).root_node, src, enclosing=None, out=out)
    return out


def _walk_fields(node, src: bytes, enclosing: str | None, out: dict[str, list[str]]) -> None:
    qualifier = enclosing
    if node.type in _TYPE_KINDS:
        name = _name(node, src)
        if name:
            qualifier = name  # fields below here belong to this type (nested types re-qualify)
    elif node.type == "field_declaration" and enclosing:
        text = " ".join(src[node.start_byte : node.end_byte].decode("utf-8", "replace").split())
        out.setdefault(enclosing, []).append(text)
    for child in node.children:
        _walk_fields(child, src, qualifier, out)


def parse_edges(source: str) -> list[EdgeData]:
    """Parse a Java file's relations (imports + calls). Pure — syntactic, no type resolution.

    ``contains`` (type → its methods) is derived from fragments at index time, not here.
    """
    src = source.encode("utf-8")
    root = _PARSER.parse(src).root_node
    out: list[EdgeData] = []
    primary = next((_name(n, src) for n in root.children if n.type in _TYPE_KINDS), None)
    if primary:
        for imp in root.children:
            if imp.type == "import_declaration":
                fqn = next(
                    (
                        src[c.start_byte : c.end_byte].decode("utf-8", "replace")
                        for c in imp.children
                        if c.type in ("scoped_identifier", "identifier")
                    ),
                    None,
                )
                if fqn:
                    out.append(EdgeData(primary, fqn, "imports"))
    _walk_calls(root, src, enclosing_type=None, enclosing_method=None, out=out)
    return out


def _walk_calls(node, src: bytes, enclosing_type, enclosing_method, out: list[EdgeData]) -> None:
    if node.type in _TYPE_KINDS:
        enclosing_type = _name(node, src) or enclosing_type
    elif node.type in _METHOD_KINDS:
        name = _name(node, src)
        enclosing_method = f"{enclosing_type}.{name}" if enclosing_type and name else name
    elif node.type == "method_invocation":
        callee = node.child_by_field_name("name")
        if enclosing_method and callee is not None:
            name = src[callee.start_byte : callee.end_byte].decode("utf-8", "replace")
            out.append(EdgeData(enclosing_method, name, "calls"))
    for child in node.children:
        _walk_calls(child, src, enclosing_type, enclosing_method, out)
