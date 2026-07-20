"""Unit tests for the rollup pass — the pure parts (tree, order, kind, digest, prompt, md).

No Ollama / no DB. The one LLM call (:func:`rollup.generate_note`) and the DB read/write
(:func:`indexer.rollup_repo`) are exercised by the opt-in golden lane.
"""

from __future__ import annotations

from code_context.indexer import rollup

# Leaf notes as (file_path, symbol, body) — the shape indexer._load_leaf_notes returns.
LEAVES = [
    ("auth/PasswordHasher.java", "PasswordHasher", "Hashes and verifies login passwords."),
    ("billing/InvoiceService.java", "InvoiceService", "Totals invoices, applies tax."),
    ("billing/tax/TaxPolicy.java", "TaxPolicy", "Computes tax for an amount."),
    ("Main.java", "Main", "Application entry point."),
]


def test_parent_dir():
    assert rollup.parent_dir("billing/InvoiceService.java") == "billing"
    assert rollup.parent_dir("billing/tax/TaxPolicy.java") == "billing/tax"
    assert rollup.parent_dir("Main.java") == ""  # a root file


def test_tree_registers_ancestors_and_links_children():
    tree = rollup.build_tree(LEAVES)
    assert set(tree) == {"", "auth", "billing", "billing/tax"}
    assert set(tree[""].children) == {"auth", "billing"}  # root file adds no dir child
    assert tree["billing"].children == ["billing/tax"]
    assert [leaf.name for leaf in tree["billing"].leaves] == ["InvoiceService"]
    assert [leaf.name for leaf in tree[""].leaves] == ["Main"]


def test_rollup_order_is_deepest_first_root_last():
    order = rollup.rollup_order(rollup.build_tree(LEAVES))
    assert order[0] == "billing/tax"  # deepest
    assert order[-1] == ""  # root last, so its children exist before it


def test_dir_kind():
    modules = {"billing"}
    assert rollup.dir_kind("", modules) == "project"
    assert rollup.dir_kind("billing", modules) == "module"  # carries a marker
    assert rollup.dir_kind("auth", modules) == "directory"


def test_inputs_digest_is_order_independent_but_content_sensitive():
    a = rollup.NoteRef("A", "note", "does A")
    b = rollup.NoteRef("B", "directory", "groups B")
    assert rollup.inputs_digest([a, b]) == rollup.inputs_digest([b, a])  # order-independent
    changed = rollup.NoteRef("A", "note", "does A differently")
    assert rollup.inputs_digest([a, b]) != rollup.inputs_digest([changed, b])  # tracks bodies


def test_prompt_carries_the_components_and_tier():
    children = [rollup.NoteRef("InvoiceService", "note", "Totals invoices.")]
    prompt = rollup.build_prompt("billing", "module", children)
    assert "Module: billing" in prompt
    assert "InvoiceService [note]: Totals invoices." in prompt
    assert prompt.rstrip().endswith("/no_think")


def test_note_markdown_labels_the_tier():
    md = rollup.note_markdown("billing", "module", "Billing subsystem.")
    assert md.startswith("# billing")
    assert "module rollup" in md
    assert "Billing subsystem." in md


# A Java-style package chain: every level holds nothing but the next one, until real classes appear.
DEEP_LEAVES = [
    ("src/main/java/com/example/app/billing/claim/ClaimService.java", "ClaimService", "Registers claims."),
    ("src/main/java/com/example/app/billing/decision/DecisionService.java", "DecisionService", "Decides."),
]


def test_collapse_removes_pass_through_directories():
    """Seven levels of one-child packages must cost one rollup, not seven re-tellings."""
    tree = rollup.build_tree(DEEP_LEAVES)
    assert len(tree) == 10  # root + 7 package levels + the two leaf dirs

    kept = rollup.collapse_chains(tree, module_dirs=set())

    assert set(kept) == {
        "",
        "src/main/java/com/example/app/billing",  # the branch point — two children, kept
        "src/main/java/com/example/app/billing/claim",
        "src/main/java/com/example/app/billing/decision",
    }
    # The root now links straight to the surviving descendant, which keeps its full path.
    assert kept[""].children == ["src/main/java/com/example/app/billing"]


def test_collapse_keeps_module_markers_and_the_root():
    """A marker dir carries the `module` tier even when it only forwards — collapsing loses that."""
    tree = rollup.build_tree(DEEP_LEAVES)
    kept = rollup.collapse_chains(tree, module_dirs={"src/main/java/com"})

    assert "src/main/java/com" in kept  # forwards a single child, but it is a module
    assert "" in kept  # the project tier is never collapsed
    assert rollup.dir_kind("src/main/java/com", {"src/main/java/com"}) == "module"


def test_collapse_keeps_directories_that_carry_their_own_classes():
    """A one-child directory that also holds classes is not a pass-through — it has content."""
    leaves = DEEP_LEAVES + [
        ("src/main/java/com/example/app/billing/Bootstrap.java", "Bootstrap", "Wires the app."),
        ("src/main/java/com/Marker.java", "Marker", "A class high in the chain."),
    ]
    kept = rollup.collapse_chains(rollup.build_tree(leaves), module_dirs=set())
    assert "src/main/java/com" in kept  # has a leaf of its own


def test_collapse_is_a_noop_on_a_branching_tree():
    tree = rollup.build_tree(LEAVES)
    assert set(rollup.collapse_chains(tree, module_dirs=set())) == set(tree)


def test_collapse_does_not_mutate_the_input_tree():
    tree = rollup.build_tree(DEEP_LEAVES)
    before = {p: list(n.children) for p, n in tree.items()}
    rollup.collapse_chains(tree, module_dirs=set())
    assert {p: list(n.children) for p, n in tree.items()} == before
