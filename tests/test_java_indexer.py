"""Unit tests for the Java chunker — pure tree-sitter, no DB / Ollama, so CI-safe."""

from __future__ import annotations

from code_context.indexer import java

SRC = """\
package a.b;

import x.Y;

public class Foo extends Bar {
    private int n;

    public Foo(int n) {
        this.n = n;
    }

    public int add(int a, int b) {
        return a + b;
    }
}
"""


def _by_symbol(frags):
    return {f.symbol: f for f in frags}


def test_extracts_type_and_methods():
    frags = _by_symbol(java.parse_source(SRC))
    assert set(frags) == {"Foo", "Foo.Foo", "Foo.add"}
    assert frags["Foo"].kind == "class"
    assert frags["Foo.add"].kind == "method"


def test_type_fragment_is_the_header_not_the_body():
    foo = _by_symbol(java.parse_source(SRC))["Foo"]
    assert foo.signature == "public class Foo extends Bar"
    assert foo.content == foo.signature  # a type embeds its header, not the whole body
    assert "add(" not in foo.content


def test_method_fragment_carries_its_body_and_lines():
    add = _by_symbol(java.parse_source(SRC))["Foo.add"]
    assert "return a + b;" in add.content
    assert add.signature.startswith("public int add(int a, int b)")
    assert add.line_start == 12 and add.line_end == 14


def test_class_fields_extracts_declarations_not_params_or_locals():
    fields = java.class_fields(SRC)
    assert fields["Foo"] == ["private int n;"]  # the field only
    # the ctor param `int n` and any method locals are not field_declarations, so they don't appear
    assert all("int a" not in f and "int b" not in f for f in fields["Foo"])


def test_empty_source_yields_nothing():
    assert java.parse_source("") == []


EDGE_SRC = """\
package a.b;

import x.y.Helper;
import x.y.Unused;

public class Svc {
    public int run(int n) {
        return new Helper().scale(n);
    }
}
"""


def _edges(kind):
    return [(e.src_symbol, e.dst_symbol) for e in java.parse_edges(EDGE_SRC) if e.kind == kind]


def test_import_edges_use_the_primary_type_and_fqn():
    assert set(_edges("imports")) == {("Svc", "x.y.Helper"), ("Svc", "x.y.Unused")}


def test_call_edges_are_from_the_enclosing_method_by_name():
    calls = _edges("calls")
    assert ("Svc.run", "scale") in calls


def test_no_edges_without_a_type():
    assert java.parse_edges("import a.B;") == []
