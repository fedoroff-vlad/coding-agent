"""Indexer — builds the derived ``code.*`` index from a repo.

Three passes over the same tree-sitter parser facts:
- :func:`index_repo` (C-2): facts -> embeddings -> ``code.fragment`` + ``code.edge`` (the retrieval
  skeleton). Cheap, run often, incremental.
- :func:`enrich_repo` (C-4a): a *leaf LLM note* per non-trivial class, written as md (md-as-source,
  Layer 1) and indexed as a ``note`` fragment. The "pay once for quality" pass — model-gated, run
  when facts settle.
- :func:`rollup_repo` (C-4b): *bottom-up* directory→module→project notes synthesized over the leaf
  notes (a strong ``rollup_model``); md-as-source + ``directory``/``module``/``project`` fragments.
- :func:`ingest_docs` (C-3): exported HTML docs -> ``doc`` fragments (``source='docs'``). No LLM.
- :func:`link_docs` (C-3 / D-3): doc text -> ``mentions`` edges onto the classes it names, so
  "which rules govern this class" is the same 1-hop join as ``find_usages``.

Incremental: a fragment whose content hash is unchanged is skipped (not re-embedded / re-noted); a
changed leaf re-flows up the rollup tree.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from .. import db, embeddings, obs
from ..config import settings
from . import docs, java, notes, rollup

_SKIP_DIRS = ("/target/", "/build/", "/.git/", "/generated-sources/")
_EMBED_BATCH = 64
# Edge kinds the Java parser owns. ``index_repo`` replaces its whole edge set on every run, so it
# must delete only these: ``mentions`` edges come from the docs pass, whose inputs it never read.
_PARSER_EDGE_KINDS = ("calls", "imports", "contains")
_TIER_PLURAL = {"directory": "directories", "module": "modules", "project": "projects"}


def _java_files(scan_root: Path, root: Path, include_tests: bool) -> list[tuple[Path, str]]:
    """The repo's Java sources as (absolute path, repo-relative posix path), skipping build output."""
    return [
        (p, p.relative_to(root).as_posix())
        for p in scan_root.rglob("*.java")
        if not any(s in p.as_posix() for s in _SKIP_DIRS)
        and (include_tests or "/src/test/" not in p.as_posix())
    ]


def index_repo(
    repo_path: str,
    repo: str | None = None,
    subpath: str | None = None,
    include_tests: bool = False,
) -> dict[str, int]:
    """Index Java sources under ``repo_path`` into ``code.fragment``.

    Returns counts: files scanned, fragments parsed, fragments (re)embedded.
    """
    root = Path(repo_path)
    repo = repo or root.name
    scan_root = root / subpath if subpath else root

    rows: list[tuple[str, java.FragmentData]] = []
    edges: list[tuple[str, java.EdgeData]] = []
    files = _java_files(scan_root, root, include_tests)
    for f, rel in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        frags = java.parse_source(text)
        rows.extend((rel, frag) for frag in frags)
        edges.extend((rel, e) for e in java.parse_edges(text))
        classes = {frag.symbol for frag in frags if frag.kind == "class"}
        for frag in frags:
            if frag.kind == "method" and frag.symbol.rsplit(".", 1)[0] in classes:
                parent = frag.symbol.rsplit(".", 1)[0]
                edges.append((rel, java.EdgeData(parent, frag.symbol, "contains")))

    existing = _existing_hashes(repo)
    todo = [
        (rel, frag, h)
        for rel, frag in rows
        if (h := _hash(frag.content)) != existing.get((rel, frag.kind, frag.symbol))
    ]

    for i in range(0, len(todo), _EMBED_BATCH):
        batch = todo[i : i + _EMBED_BATCH]
        vectors = embeddings.embed([frag.content for _, frag, _ in batch])
        _upsert(repo, batch, vectors)

    n_edges = _rebuild_edges(repo, edges)
    return {"files": len(files), "fragments": len(rows), "indexed": len(todo), "edges": n_edges}


def enrich_repo(
    repo_path: str,
    repo: str | None = None,
    subpath: str | None = None,
    include_tests: bool = False,
) -> dict[str, int]:
    """Write a leaf LLM note per non-trivial class, as md (Layer 1) + a ``note`` fragment (C-4).

    Model-gated (one LLM call per changed class), so run it after the facts pass settles.
    Incremental on the parser facts (:func:`notes.facts_key`): a class whose signatures are
    unchanged keeps its note and is not re-generated. Notes for classes that vanished (deleted or
    turned trivial) are dropped. Returns counts: classes seen / trivial / noted / skipped.
    """
    root = Path(repo_path)
    repo = repo or root.name
    scan_root = root / subpath if subpath else root
    notes_root = Path(settings.notes_root) if settings.notes_root else root / ".code-context" / "notes"

    existing = _existing_hashes(repo)
    pending: list[tuple[str, notes.ClassUnit, str, str]] = []  # (rel, unit, body, key_hash)
    seen: dict[str, set[str]] = {}
    stats = {"classes": 0, "trivial": 0, "noted": 0, "skipped": 0}

    for f, rel in _java_files(scan_root, root, include_tests):
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        units = notes.class_units(java.parse_source(text))
        stats["classes"] += len(units)
        seen[rel] = set()
        for unit in units:
            if notes.is_trivial(unit):
                stats["trivial"] += 1
                obs.event("enrich.skip", logging.DEBUG, input=f.name, symbol=unit.cls.symbol,
                          reason="trivial")
                continue
            sym = unit.cls.symbol
            seen[rel].add(sym)
            key_hash = _hash(notes.facts_key(unit))
            if key_hash == existing.get((rel, "note", sym)):
                stats["skipped"] += 1
                obs.event("enrich.skip", logging.DEBUG, input=f.name, symbol=sym,
                          reason="unchanged")
                continue
            md_path = notes_root / rel / f"{sym}.md"
            # Names only: the input file and the md we produce — never the class body or the note.
            with obs.timed("enrich.note", logging.DEBUG, input=f.name, symbol=sym,
                           output=md_path.name):
                body = notes.generate_note(unit, rel)
            if not body:
                continue
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(notes.note_markdown(unit, rel, body), encoding="utf-8")
            pending.append((rel, unit, body, key_hash))

    for i in range(0, len(pending), _EMBED_BATCH):
        batch = pending[i : i + _EMBED_BATCH]
        vectors = embeddings.embed([body for _, _, body, _ in batch])
        _upsert_notes(repo, batch, vectors)

    stats["noted"] = len(pending)
    _prune_notes(repo, seen)
    return stats


#: Document formats the docs pass reads. HTML is the Confluence export (D-2); `.docx` (D-6) and
#: `.pdf` (D-7) are converted to markdown first. Kept as one set so the glob, the dispatch and the
#: docs stay in step — a format in the glob but not the dispatch is silently skipped.
_DOC_SUFFIXES = {".html", ".htm", ".docx", ".pdf"}
#: Suffix → the converter that turns that binary into markdown before it is chunked.
_TO_MARKDOWN = {".docx": docs.docx_to_markdown, ".pdf": docs.pdf_to_markdown}


def _document_files(root: Path) -> list[Path]:
    """The source documents under ``root``, in a stable order.

    Two exclusions carry their weight:

    - ``~$name.docx`` — Word's lock file for an open document. It is a few hundred bytes of
      metadata, not a document, and ingesting one produces a garbage fragment attributed to a file
      the author cannot even see.
    - anything under ``.code-context`` — our own converted markdown lives there by default, and a
      corpus that re-ingests its own output grows a duplicate of every document it converts.
    """
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in _DOC_SUFFIXES
        and not p.name.startswith("~$")
        and ".code-context" not in p.parts
    )


def _parse_document(path: Path, root: Path) -> list[docs.Section] | None:
    """One source file → sections, converting to markdown first when the format demands it.

    Returns ``None`` when the file cannot be read or converted: one bad document must not abort a
    corpus-wide ingest, but it must not pass silently either — a doc nobody can retrieve looks
    exactly like a doc nobody wrote.

    A converted ``.docx``/``.pdf`` is **written out as markdown** next to the corpus
    (``.code-context/md/<rel>.md``, or ``CODE_CONTEXT_DOCS_MD_ROOT``) before it is chunked. That
    file is the Layer-1 record: the binary is neither greppable nor diffable, so without it the
    only readable form of the document would be rows in a database — and md-as-source is the same
    rule the notes pass already follows.
    """
    suffix = path.suffix.lower()
    convert = _TO_MARKDOWN.get(suffix)
    try:
        if convert is not None:
            with obs.timed("docs.convert", logging.DEBUG, input=path.name, format=suffix) as ev:
                md = convert(path.read_bytes())
                ev["chars"] = len(md)
            if not md.strip():
                # A .pdf reaches here when it is a scan: images, no text layer. Saying so is the
                # point — OCR is not wired up (deliberately), and a document nobody can read must
                # not be indistinguishable from one that was read and found empty.
                obs.event("docs.convert", logging.WARNING, "no text extracted", input=path.name,
                          format=suffix, outcome="empty")
                return None
            _write_doc_markdown(path, root, md)
            with obs.timed("docs.parse", logging.DEBUG, input=path.name) as ev:
                sections = docs.parse_markdown(md, max_chars=settings.docs_max_chars)
                ev["sections"] = len(sections)
            return sections
        with obs.timed("docs.parse", logging.DEBUG, input=path.name) as ev:
            sections = docs.parse_html(
                path.read_text(encoding="utf-8"), max_chars=settings.docs_max_chars
            )
            ev["sections"] = len(sections)
        return sections
    except Exception as exc:
        # Deliberately broad, and only around a *single* file. Each reader raises its own
        # hierarchy — a `.doc` renamed to `.docx` comes back as zipfile.BadZipFile, which is not
        # an OSError — so enumerating types here means the next format we add silently reintroduces
        # "one malformed file aborts a corpus of thousands". The type is kept on the event, so a
        # skipped document is diagnosable rather than merely survived.
        obs.event("docs.parse", logging.WARNING, "unreadable document", input=path.name,
                  outcome="error", error=type(exc).__name__)
        return None


def _write_doc_markdown(path: Path, root: Path, md: str) -> None:
    """Persist the converted markdown (Layer 1). A write failure degrades to a warn, not a stop."""
    md_root = Path(settings.docs_md_root) if settings.docs_md_root else root / ".code-context/md"
    out = md_root / path.relative_to(root).with_suffix(".md")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
    except OSError as exc:
        obs.event("docs.convert", logging.WARNING, "markdown not written", input=path.name,
                  outcome="error", error=type(exc).__name__)


def ingest_docs(docs_path: str, repo: str | None = None) -> dict[str, int]:
    """Ingest exported docs under ``docs_path`` into ``code.fragment`` (``kind='doc'``).

    Handles exported HTML pages and (D-6) ``.docx``. ``repo`` scopes the corpus. **Pass the code
    repo's name** to put docs and code in one scope — that is what lets D-3 link a rule to the class
    it governs, and what keeps `search_docs` answering about the project you are working on. It
    defaults to the docs directory's name, which is only right for a standalone corpus.

    Incremental on the section's content hash. No LLM: parse → chunk → embed → upsert, so this pass
    runs comfortably on hardware that cannot afford generation.
    """
    root = Path(docs_path)
    repo = repo or root.name
    files = _document_files(root)

    existing = _existing_hashes(repo)
    pending: list[tuple[str, docs.Section, str]] = []  # (rel, section, hash)
    seen: dict[str, set[str]] = {}
    stats = {"files": 0, "sections": 0, "ingested": 0, "skipped": 0}

    for f in files:
        rel = f.relative_to(root).as_posix()
        sections = _parse_document(f, root)
        if sections is None:  # unreadable / unconvertible — already reported, keep going
            continue
        stats["files"] += 1
        stats["sections"] += len(sections)
        seen[rel] = set()
        for section in sections:
            seen[rel].add(section.symbol)
            h = _hash(section.text)
            if h == existing.get((rel, "doc", section.symbol)):
                stats["skipped"] += 1
                continue
            pending.append((rel, section, h))

    for i in range(0, len(pending), _EMBED_BATCH):
        batch = pending[i : i + _EMBED_BATCH]
        vectors = embeddings.embed([s.text for _, s, _ in batch])
        _upsert_docs(repo, batch, vectors)

    stats["ingested"] = len(pending)
    _prune_docs(repo, seen)
    obs.event("docs.ingest", repo=repo, **stats)
    stats["links"] = link_docs(repo)["links"]
    return stats


def link_docs(repo: str) -> dict[str, int]:
    """Link doc fragments to the classes they name — ``code.edge`` rows with ``kind='mentions'``.

    This is the half that makes the docs corpus more than a second search box: both directions
    become answerable over the *existing* 1-hop join — *which rules govern this class* and *which
    code implements this rule* — which is the same reasoning that kept a graph store out.

    Run after both passes have seen the repo; order does not matter, but a link needs the class to
    be indexed, so ``ingest_docs`` calls this at the end and ``dev link`` re-runs it after a code
    index catches up. Idempotent: the repo's ``mentions`` edges are rebuilt, never appended to.

    Precision: matching is by **simple name**, the same syntactic limitation as ``find_usages``
    (real resolution is the Java sidecar, C-8), and it is narrowed further by
    :func:`docs.is_linkable` — single-word class names are ordinary domain words in a business
    wiki and are deliberately not linked. Two classes with the same simple name in different
    packages both get an edge; the caller sees the paths and decides.
    """
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, path, symbol, content FROM code.fragment "
            "WHERE repo = %s AND kind = 'doc'",
            (repo,),
        )
        doc_rows = cur.fetchall()
        cur.execute(
            "SELECT symbol, id FROM code.fragment WHERE repo = %s AND kind = 'class' "
            "AND symbol IS NOT NULL",
            (repo,),
        )
        ids_by_symbol: dict[str, list[int]] = {}
        for symbol, fid in cur.fetchall():
            if docs.is_linkable(symbol):
                ids_by_symbol.setdefault(symbol, []).append(fid)

    linkable = set(ids_by_symbol)
    edges: set[tuple[int, int]] = set()
    matched_docs = 0
    for fid, path, symbol, content in doc_rows:
        names = docs.find_mentions(content or "", linkable)
        if not names:
            continue
        matched_docs += 1
        edges.update((fid, cid) for n in names for cid in ids_by_symbol[n])
        # Names and counts only — the doc text is customer content (§Observability).
        obs.event("docs.link", logging.DEBUG, input=path, symbol=symbol, mentions=len(names))

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM code.edge WHERE kind = 'mentions' AND src_id IN "
            "(SELECT id FROM code.fragment WHERE repo = %s AND kind = 'doc')",
            (repo,),
        )
        cur.executemany(
            "INSERT INTO code.edge (src_id, dst_id, dst_symbol, kind) "
            "VALUES (%s, %s, NULL, 'mentions') "
            "ON CONFLICT (src_id, dst_id, dst_symbol, kind) DO NOTHING",
            sorted(edges),
        )
        conn.commit()

    stats = {"docs": len(doc_rows), "linked_docs": matched_docs, "classes": len(linkable),
             "links": len(edges)}
    obs.event("docs.link_pass", repo=repo, **stats)
    return stats


def _upsert_docs(
    repo: str,
    batch: list[tuple[str, docs.Section, str]],
    vectors: list[list[float]],
) -> None:
    """Upsert doc fragments.

    ``source='docs'`` is load-bearing, not bookkeeping: it is how every downstream consumer tells
    ingested (untrusted, editable by anyone) content from code-derived content. ``signature`` holds
    the heading path so a retrieved slice can show its provenance without a second query.
    """
    sql = (
        "INSERT INTO code.fragment "
        "(repo, path, kind, symbol, signature, lang, source, content, embedding, content_hash) "
        "VALUES (%s, %s, 'doc', %s, %s, 'html', 'docs', %s, %s::vector, %s) "
        "ON CONFLICT (repo, path, kind, symbol) DO UPDATE SET "
        "  signature = EXCLUDED.signature, content = EXCLUDED.content, "
        "  embedding = EXCLUDED.embedding, content_hash = EXCLUDED.content_hash, "
        "  updated_at = now()"
    )
    with db.connect() as conn, conn.cursor() as cur:
        for (rel, section, h), vec in zip(batch, vectors, strict=True):
            cur.execute(
                sql,
                (
                    repo, rel, section.symbol, " / ".join(section.heading_path),
                    section.text, embeddings.to_literal(vec), h,
                ),
            )
        conn.commit()


def _prune_docs(repo: str, seen: dict[str, set[str]]) -> None:
    """Drop doc fragments for sections that no longer exist — a page edited down leaves stale slices.

    Only touches documents seen in this run, so ingesting one directory never deletes another's.
    """
    if not seen:
        return
    removed = 0
    with db.connect() as conn, conn.cursor() as cur:
        for rel, symbols in seen.items():
            cur.execute(
                "DELETE FROM code.fragment WHERE repo = %s AND path = %s AND kind = 'doc' "
                "AND NOT (symbol = ANY(%s))",
                (repo, rel, list(symbols)),
            )
            removed += cur.rowcount
        conn.commit()
    if removed:
        obs.event("docs.prune", removed=removed)


def _upsert_notes(
    repo: str,
    batch: list[tuple[str, notes.ClassUnit, str, str]],
    vectors: list[list[float]],
) -> None:
    """Upsert leaf-note fragments (``kind='note'``, ``source='llm'``), anchored to the source path."""
    sql = (
        "INSERT INTO code.fragment "
        "(repo, path, kind, symbol, signature, line_start, line_end, lang, source, "
        " content, embedding, content_hash) "
        "VALUES (%s, %s, 'note', %s, %s, %s, %s, 'java', 'llm', %s, %s::vector, %s) "
        "ON CONFLICT (repo, path, kind, symbol) DO UPDATE SET "
        "  signature = EXCLUDED.signature, line_start = EXCLUDED.line_start, "
        "  line_end = EXCLUDED.line_end, content = EXCLUDED.content, "
        "  embedding = EXCLUDED.embedding, content_hash = EXCLUDED.content_hash, "
        "  updated_at = now()"
    )
    with db.connect() as conn, conn.cursor() as cur:
        for (rel, unit, body, key_hash), vec in zip(batch, vectors, strict=True):
            cur.execute(
                sql,
                (
                    repo, rel, unit.cls.symbol, unit.cls.signature,
                    unit.cls.line_start, unit.cls.line_end, body,
                    embeddings.to_literal(vec), key_hash,
                ),
            )
        conn.commit()


def _prune_notes(repo: str, seen: dict[str, set[str]]) -> None:
    """Drop note fragments for classes no longer present (deleted or turned trivial) in scanned files."""
    if not seen:
        return
    with db.connect() as conn, conn.cursor() as cur:
        for rel, symbols in seen.items():
            cur.execute(
                "DELETE FROM code.fragment WHERE repo = %s AND path = %s AND kind = 'note' "
                "AND NOT (symbol = ANY(%s))",
                (repo, rel, list(symbols)),
            )
        conn.commit()


def rollup_repo(
    repo_path: str,
    repo: str | None = None,
) -> dict[str, int]:
    """Synthesize directory→module→project notes bottom-up over the leaf notes (C-4b).

    Reads the ``note`` fragments :func:`enrich_repo` wrote, builds the directory tree, and rolls each
    directory up from its components (child rollups + own leaf notes) with ``rollup_model``; the repo
    root becomes the ``project`` note, marker dirs (``module_markers``) become ``module`` notes.
    Incremental on each directory's input digest — an unchanged directory keeps its note (and its body
    still feeds its parent); rollups for vanished directories are dropped. Returns counts by tier.
    """
    root = Path(repo_path)
    repo = repo or root.name
    notes_root = Path(settings.notes_root) if settings.notes_root else root / ".code-context" / "notes"

    leaves = _load_leaf_notes(repo)
    if not leaves:
        return {"directories": 0, "modules": 0, "projects": 0, "rolled": 0, "skipped": 0}
    tree = rollup.build_tree(leaves)
    module_dirs = {
        d for d in tree if d and any((root / d / m).exists() for m in settings.module_markers)
    }
    # Detect modules before collapsing: a marker dir is never a pass-through, and the check reads
    # the filesystem, which the collapsed tree no longer describes.
    full_tree_dirs = set(tree)
    tree = rollup.collapse_chains(tree, module_dirs)
    if collapsed := len(full_tree_dirs) - len(tree):
        obs.event("rollup.collapse", input=repo, collapsed=collapsed, remaining=len(tree))
    existing = _load_rollups(repo)  # dirpath -> (content, content_hash)

    computed: dict[str, str] = {}  # dirpath -> note body (for parents to consume)
    pending: list[tuple[str, str, str, str]] = []  # (dirpath, kind, body, digest)
    stats = {"directories": 0, "modules": 0, "projects": 0, "rolled": 0, "skipped": 0}

    for dirpath in rollup.rollup_order(tree):
        node = tree[dirpath]
        children = [
            rollup.NoteRef(name=c, kind=rollup.dir_kind(c, module_dirs), body=computed[c])
            for c in node.children
            if c in computed
        ] + list(node.leaves)
        if not children:
            continue
        kind = rollup.dir_kind(dirpath, module_dirs)
        stats[_TIER_PLURAL[kind]] += 1
        digest = rollup.inputs_digest(children)
        prev = existing.get(dirpath)
        if prev and prev[1] == digest:
            computed[dirpath] = prev[0]
            stats["skipped"] += 1
            obs.event("rollup.skip", logging.DEBUG, input=dirpath or ".", kind=kind,
                      reason="unchanged")
            continue
        # The failing node is named up front: a timeout here used to be reconstructable only from
        # which files happened to exist on disk.
        with obs.timed("rollup.note", logging.DEBUG, input=dirpath or ".", kind=kind,
                       children=len(children), output="_index.md"):
            body = rollup.generate_note(dirpath, kind, children, settings.rollup_model)
        if not body:
            continue
        computed[dirpath] = body
        md_path = notes_root / dirpath / "_index.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(rollup.note_markdown(dirpath, kind, body), encoding="utf-8")
        pending.append((dirpath, kind, body, digest))

    for i in range(0, len(pending), _EMBED_BATCH):
        batch = pending[i : i + _EMBED_BATCH]
        vectors = embeddings.embed([body for _, _, body, _ in batch])
        _upsert_rollups(repo, batch, vectors)

    stats["rolled"] = len(pending)
    live = set(tree) & set(computed)
    _prune_rollups(repo, live)
    stats["pruned_md"] = _prune_rollup_md(notes_root, live)
    return stats


def _prune_rollup_md(notes_root: Path, live_dirs: set[str]) -> int:
    """Delete ``_index.md`` files for directories that no longer roll up.

    md is the source-of-truth layer, so a stale rollup is worse than a missing one: nothing marks it
    as dead, and an agent reading the tree takes it as current. Collapsed pass-through directories
    make this reachable on any existing checkout, not just after a delete.
    """
    if not notes_root.exists():
        return 0
    removed = 0
    for md in notes_root.rglob("_index.md"):
        dirpath = md.parent.relative_to(notes_root).as_posix()
        if (dirpath if dirpath != "." else "") not in live_dirs:
            md.unlink()
            removed += 1
    if removed:
        obs.event("rollup.prune_md", removed=removed)
    return removed


def _load_leaf_notes(repo: str) -> list[tuple[str, str, str]]:
    """Leaf notes as ``(file_path, symbol, body)`` — the inputs to the rollup tree."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT path, symbol, content FROM code.fragment WHERE repo = %s AND kind = 'note'",
            (repo,),
        )
        return [(p, s, c) for p, s, c in cur.fetchall()]


def _load_rollups(repo: str) -> dict[str, tuple[str, str]]:
    """Existing rollup fragments keyed by dir path (root stored as ``.``) → (content, content_hash)."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT path, content, content_hash FROM code.fragment "
            "WHERE repo = %s AND kind = ANY(%s)",
            (repo, list(rollup.ROLLUP_KINDS)),
        )
        return {("" if p == "." else p): (c, h) for p, c, h in cur.fetchall()}


def _upsert_rollups(
    repo: str,
    batch: list[tuple[str, str, str, str]],
    vectors: list[list[float]],
) -> None:
    """Upsert rollup fragments (``kind`` in directory/module/project, ``source='llm'``)."""
    sql = (
        "INSERT INTO code.fragment "
        "(repo, path, kind, symbol, lang, source, content, embedding, content_hash) "
        "VALUES (%s, %s, %s, %s, 'java', 'llm', %s, %s::vector, %s) "
        "ON CONFLICT (repo, path, kind, symbol) DO UPDATE SET "
        "  content = EXCLUDED.content, embedding = EXCLUDED.embedding, "
        "  content_hash = EXCLUDED.content_hash, updated_at = now()"
    )
    with db.connect() as conn, conn.cursor() as cur:
        for (dirpath, kind, body, digest), vec in zip(batch, vectors, strict=True):
            path = dirpath or "."
            symbol = dirpath or repo
            cur.execute(sql, (repo, path, kind, symbol, body, embeddings.to_literal(vec), digest))
        conn.commit()


def _prune_rollups(repo: str, live_dirs: set[str]) -> None:
    """Drop rollup fragments for directories that no longer roll up (deleted / all-trivial subtree)."""
    live_paths = [d or "." for d in live_dirs]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM code.fragment WHERE repo = %s AND kind = ANY(%s) "
            "AND NOT (path = ANY(%s))",
            (repo, list(rollup.ROLLUP_KINDS), live_paths),
        )
        conn.commit()


def _rebuild_edges(repo: str, edges: list[tuple[str, java.EdgeData]]) -> int:
    """Resolve edges to fragment ids and replace the repo's edge set (cheap — no embeddings)."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, path, symbol FROM code.fragment WHERE repo = %s", (repo,))
        id_by = {(path, symbol): fid for fid, path, symbol in cur.fetchall()}

        resolved: set[tuple[int, int | None, str | None, str]] = set()
        for rel, e in edges:
            src_id = id_by.get((rel, e.src_symbol))
            if src_id is None:
                continue
            dst_id = id_by.get((rel, e.dst_symbol)) if e.kind == "contains" else None
            resolved.add((src_id, dst_id, None if dst_id else e.dst_symbol, e.kind))

        # Scoped by kind on purpose: an unscoped delete would drop the docs pass's ``mentions``
        # edges on every code re-index, and nothing would report it — the links would just be gone
        # until someone re-ran `dev link`.
        cur.execute(
            "DELETE FROM code.edge WHERE kind = ANY(%s) "
            "AND src_id IN (SELECT id FROM code.fragment WHERE repo = %s)",
            (list(_PARSER_EDGE_KINDS), repo),
        )
        cur.executemany(
            "INSERT INTO code.edge (src_id, dst_id, dst_symbol, kind) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (src_id, dst_id, dst_symbol, kind) DO NOTHING",
            list(resolved),
        )
        conn.commit()
    return len(resolved)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _existing_hashes(repo: str) -> dict[tuple[str, str, str], str]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT path, kind, symbol, content_hash FROM code.fragment WHERE repo = %s",
            (repo,),
        )
        return {(p, k, s): h for p, k, s, h in cur.fetchall()}


def _upsert(
    repo: str,
    batch: list[tuple[str, java.FragmentData, str]],
    vectors: list[list[float]],
) -> None:
    sql = (
        "INSERT INTO code.fragment "
        "(repo, path, kind, symbol, signature, line_start, line_end, lang, source, "
        " content, embedding, content_hash) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'java', 'facts', %s, %s::vector, %s) "
        "ON CONFLICT (repo, path, kind, symbol) DO UPDATE SET "
        "  signature = EXCLUDED.signature, line_start = EXCLUDED.line_start, "
        "  line_end = EXCLUDED.line_end, content = EXCLUDED.content, "
        "  embedding = EXCLUDED.embedding, content_hash = EXCLUDED.content_hash, "
        "  updated_at = now()"
    )
    with db.connect() as conn, conn.cursor() as cur:
        for (rel, frag, h), vec in zip(batch, vectors, strict=True):
            cur.execute(
                sql,
                (
                    repo, rel, frag.kind, frag.symbol, frag.signature,
                    frag.line_start, frag.line_end, frag.content,
                    embeddings.to_literal(vec), h,
                ),
            )
        conn.commit()
