"""LLM note rollups — the directory→module→project tiers of enrichment (roadmap C-4b).

Bottom-up over the C-4a *leaf* notes: a directory's note synthesizes its components (the rollup
notes of child directories + the leaf notes of the classes directly in it); the repo root's rollup
is the **project** note, a directory carrying a build marker (``pom.xml`` …) is a **module**, the rest
are **directory** notes. The tiers reuse the schema's own ``kind`` vocabulary — no migration.

The roadmap escalates this tier to a strong model (``rollup_model``) for cross-file reasoning; the
pipeline is model-agnostic, so it still runs fully local. md-as-source; incremental on the digest of
a directory's inputs, so a changed leaf re-flows up the tree.

Pure except :func:`generate_note` (the one LLM call): the tree, ordering, prompt, digest, and md
rendering are all testable without a live engine.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .. import llm
from ..config import settings

ROLLUP_KINDS = ("directory", "module", "project")

_SYSTEM = (
    "You are a senior engineer writing a high-level map of a codebase for a retrieval index. From "
    "the notes of a directory's components, summarize what that directory/module/project does AS A "
    "WHOLE. Be terse and factual, synthesize (don't list every component), and never invent parts "
    "that aren't in the given components."
)


@dataclass(frozen=True)
class NoteRef:
    """One component feeding a rollup: a leaf class note, or a child directory's rollup."""

    name: str  # the class symbol, or the child directory's path
    kind: str  # note | directory | module | project
    body: str


@dataclass
class DirNode:
    """A directory in the rollup tree. ``path`` is repo-relative posix; ``""`` is the repo root."""

    path: str
    leaves: list[NoteRef] = field(default_factory=list)
    children: list[str] = field(default_factory=list)  # child directory paths


def parent_dir(file_path: str) -> str:
    """The directory of a file path (posix). ``"a/b/C.java" → "a/b"``; a root file → ``""``."""
    parent = PurePosixPath(file_path).parent
    return "" if str(parent) == "." else str(parent)


def _ancestors(dirpath: str) -> list[str]:
    """A directory and its ancestors, deepest first, ending at the root. ``"a/b" → ["a/b","a",""]``."""
    if dirpath == "":
        return [""]
    parts = dirpath.split("/")
    return ["/".join(parts[:i]) for i in range(len(parts), 0, -1)] + [""]


def build_tree(leaves: list[tuple[str, str, str]]) -> dict[str, DirNode]:
    """Build the directory tree from leaf notes ``(file_path, symbol, body)``.

    Registers every ancestor directory up to the root and links each child to its parent, so a
    directory with only sub-directories (no direct classes) still gets rolled up.
    """
    nodes: dict[str, DirNode] = {}

    def ensure(d: str) -> DirNode:
        return nodes.setdefault(d, DirNode(path=d))

    for path, symbol, body in leaves:
        chain = _ancestors(parent_dir(path))  # [dir, ..., ""]
        for d in chain:
            ensure(d)
        for child, parent in zip(chain, chain[1:], strict=False):
            if child not in nodes[parent].children:
                nodes[parent].children.append(child)
        nodes[chain[0]].leaves.append(NoteRef(name=symbol, kind="note", body=body))
    return nodes


def collapse_chains(nodes: dict[str, DirNode], module_dirs: set[str]) -> dict[str, DirNode]:
    """Drop pass-through directories, linking their parent straight to their single child.

    Java's package layout produces long runs of directories that hold nothing but the next one
    (``com/example/app/module/service/domain/impl`` — seven levels, one child each). Rolling each up separately
    spends an LLM call per level re-synthesizing the same content on an ever-growing prompt, and
    every re-telling blurs the meaning a little more — measured on a real repo, the root note came
    out noticeably vaguer than the tier below it.

    A directory is *pass-through* when it has no leaf notes of its own and exactly one child. Two
    are never collapsed:

    - **the root**, which is the ``project`` tier itself;
    - a **module marker** dir (``pom.xml``/``build.gradle``), whose tier is meaningful even when it
      only forwards — collapsing it would lose the ``module`` kind from the index.

    The surviving descendant keeps its own full path, so retrieval still resolves it.
    """
    kept = {p: DirNode(p, list(n.leaves), list(n.children)) for p, n in nodes.items()}
    parent_of = {c: p for p, n in kept.items() for c in n.children}

    def is_pass_through(path: str, node: DirNode) -> bool:
        return (
            path != ""
            and not node.leaves
            and len(node.children) == 1
            and dir_kind(path, module_dirs) != "module"
            and path in parent_of
        )

    # Repeat to a fixpoint: collapsing one link can expose the next in the same chain.
    changed = True
    while changed:
        changed = False
        for path, node in list(kept.items()):
            if not is_pass_through(path, node):
                continue
            parent, child = parent_of[path], node.children[0]
            kept[parent].children = [child if c == path else c for c in kept[parent].children]
            parent_of[child] = parent
            del kept[path], parent_of[path]
            changed = True
    return kept


def rollup_order(nodes: dict[str, DirNode]) -> list[str]:
    """Directory paths deepest-first (children before parents), root last."""
    depth = lambda d: 0 if d == "" else d.count("/") + 1  # noqa: E731
    return sorted(nodes, key=depth, reverse=True)


def dir_kind(dirpath: str, module_dirs: set[str]) -> str:
    """The fragment kind for a directory: root → ``project``, marker dir → ``module``, else ``directory``."""
    if dirpath == "":
        return "project"
    return "module" if dirpath in module_dirs else "directory"


def inputs_digest(children: list[NoteRef]) -> str:
    """A stable digest of a rollup's inputs — for incremental re-rollup (a changed input re-flows up).

    Includes the rollup model for the same reason ``notes.facts_key`` does: the analyzer is an input
    to the output, so swapping it must re-synthesize rather than silently skip.
    """
    joined = "\n".join(
        [f"model={settings.rollup_model}"]
        + [f"{c.kind}\t{c.name}\t{c.body}" for c in sorted(children, key=lambda x: (x.kind, x.name))]
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def build_prompt(dirpath: str, kind: str, children: list[NoteRef]) -> str:
    """A rollup prompt anchored to the child component notes (→ no invented subsystems)."""
    name = dirpath or "(repository root)"
    lines = "\n".join(f"- {c.name} [{c.kind}]: {c.body.strip()}" for c in children) or "- (none)"
    return (
        f"{kind.capitalize()}: {name}\n"
        f"Components:\n{lines}\n\n"
        f"Write a concise note (3-5 sentences) describing what this {kind} is responsible for as a "
        f"whole, the main areas/subsystems it groups, and how they relate. Synthesize — do not "
        f"restate every component. Output plain prose (no headings, no code fences).\n/no_think"
    )


def generate_note(dirpath: str, kind: str, children: list[NoteRef], model: str) -> str | None:
    """The LLM call for one rollup. Returns the note body (or ``None`` if there's nothing to roll up)."""
    if not children:
        return None
    body = llm.generate(
        build_prompt(dirpath, kind, children),
        system=_SYSTEM,
        model=model,
        timeout_s=settings.rollup_timeout_s,
        num_ctx=settings.rollup_num_ctx,
    )
    return body or None


def note_markdown(dirpath: str, kind: str, body: str) -> str:
    """The Layer-1 md artifact for a rollup (written to ``notes_root/<dir>/_index.md``)."""
    return (
        f"# {dirpath or '(project root)'}\n\n"
        f"- **kind:** llm-note ({kind} rollup)\n"
        f"- **path:** {dirpath or '.'}\n\n"
        f"{body}\n"
    )
