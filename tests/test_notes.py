"""Unit tests for the LLM-notes leaf pass — the pure parts (gate, prompt, markdown, digest).

No Ollama / no DB: everything here is deterministic parser-fact logic, so it runs in unit CI. The
one LLM call (:func:`notes.generate_note`) and the DB write (:func:`indexer.enrich_repo`) are
exercised by the opt-in golden lane instead.
"""

from __future__ import annotations

from code_context.indexer import java, notes

SERVICE = """\
package billing;

import billing.model.LineItem;

public class InvoiceService {
    private final TaxPolicy policy;

    public InvoiceService(TaxPolicy policy) {
        this.policy = policy;
    }

    public long total(java.util.List<LineItem> items) {
        long sum = 0;
        for (LineItem it : items) sum += it.amount();
        return policy.apply(sum);
    }
}
"""

DTO = """\
package billing.model;

public class LineItem {
    private final long amount;
    public LineItem(long amount) { this.amount = amount; }
    public long amount() { return amount; }
    public long getAmount() { return amount; }
    public String toString() { return "LineItem"; }
}
"""

RECORD = "package a; public record Point(int x, int y) {}"


def _unit(src: str, symbol: str) -> notes.ClassUnit:
    return {u.cls.symbol: u for u in notes.class_units(java.parse_source(src))}[symbol]


def test_class_units_groups_methods_under_their_type():
    unit = _unit(SERVICE, "InvoiceService")
    assert {m.symbol for m in unit.methods} == {"InvoiceService.InvoiceService", "InvoiceService.total"}


def test_substantive_drops_constructors_and_accessors():
    svc = notes.substantive_methods(_unit(SERVICE, "InvoiceService"))
    assert [m.symbol for m in svc] == ["InvoiceService.total"]  # ctor excluded
    dto = notes.substantive_methods(_unit(DTO, "LineItem"))
    assert dto == []  # getters/setters/toString are all boilerplate


def test_gate_keeps_a_service_but_skips_data_carriers():
    assert notes.is_trivial(_unit(SERVICE, "InvoiceService")) is False
    assert notes.is_trivial(_unit(DTO, "LineItem")) is True
    assert notes.is_trivial(_unit(RECORD, "Point")) is True  # a record is a data carrier


def test_prompt_is_anchored_to_real_signatures():
    prompt = notes.build_prompt(_unit(SERVICE, "InvoiceService"), "billing/InvoiceService.java")
    assert "public class InvoiceService" in prompt
    assert "long total(java.util.List<LineItem> items)" in prompt
    assert "InvoiceService(TaxPolicy policy)" not in prompt  # ctor not offered to the model
    assert prompt.rstrip().endswith("/no_think")  # thinking suppressed for qwen3


def test_facts_key_ignores_body_changes_but_tracks_signatures():
    a = notes.facts_key(_unit(SERVICE, "InvoiceService"))
    changed_body = SERVICE.replace("long sum = 0;", "long sum = 1;")  # body only
    b = notes.facts_key(_unit(changed_body, "InvoiceService"))
    assert a == b  # a note stays valid when only a method body changes
    renamed = SERVICE.replace("total(", "grandTotal(")  # signature change
    assert notes.facts_key(_unit(renamed, "InvoiceService")) != a


def test_note_markdown_carries_the_anchor():
    unit = _unit(SERVICE, "InvoiceService")
    md = notes.note_markdown(unit, "billing/InvoiceService.java", "Computes invoice totals.")
    assert md.startswith("# InvoiceService")
    assert "billing/InvoiceService.java:" in md
    assert "Computes invoice totals." in md


def test_strip_think_removes_the_reasoning_preamble():
    assert notes.llm.strip_think("<think>reasoning...</think>The note.") == "The note."
    assert notes.llm.strip_think("no preamble") == "no preamble"
