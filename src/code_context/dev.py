"""Dev helper CLI — bring up / smoke-check the local infra before the real indexer exists.

    python -m code_context.dev db-ping         # can we reach Postgres?
    python -m code_context.dev migrate          # apply pending DB migrations (idempotent)
    python -m code_context.dev embed-smoke       # can Ollama embed at the configured dim?
    python -m code_context.dev index <repo-path> # index a Java repo into code.fragment
    python -m code_context.dev enrich <repo-path> # LLM leaf notes over the facts (md + note fragments)
    python -m code_context.dev rollup <repo-path> # bottom-up dir/module/project notes over the leaves
    python -m code_context.dev ingest <docs-path> [repo]  # docs (HTML/.docx/.pdf) -> fragments (no LLM)
    python -m code_context.dev link <repo>       # doc -> class 'mentions' edges (re-run after re-index)
    python -m code_context.dev confluence-sync <SPACE> <dest-dir> [repo]  # wiki -> HTML corpus (+ingest)
    python -m code_context.dev agents-md <repo-path> [--force]  # starter AGENTS.md in the TARGET repo
    python -m code_context.dev search <query>     # semantic search over the index

Needs the dev DB up (infra/docker-compose.yml) and Ollama with the embed model pulled (``enrich`` /
``rollup`` also need the notes / rollup model — CODE_CONTEXT_NOTES_MODEL / _ROLLUP_MODEL, e.g. qwen3:8b).
"""

from __future__ import annotations

import sys

from . import db, embeddings, lifecycle, obs, tools
from .config import settings


def db_ping() -> int:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    print(f"db ok: {settings.db_dsn}")
    return 0


def migrate() -> int:
    applied = db.migrate()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s",
            (settings.db_schema,),
        )
        (n,) = cur.fetchone()
    print(f"migrate ok: applied {applied or '(up to date)'}; {n} tables in {settings.db_schema!r}")
    return 0


def embed_smoke() -> int:
    v = embeddings.embed_one("hello world")
    print(f"embed ok: model={settings.embed_model} dim={len(v)}")
    return 0


def index(repo_path: str) -> int:
    from .indexer import index_repo

    stats = index_repo(repo_path)
    print(f"index ok: {stats}")
    return 0


def enrich(repo_path: str, subpath: str | None = None) -> int:
    from .indexer import enrich_repo

    # An optional subpath scopes the pass to one module/directory (a cheap trial before the whole
    # repo) — the repo scope stays the root's name, so its notes join the same index, and pruning
    # only touches the files actually scanned (never the rest of the repo's notes).
    stats = enrich_repo(repo_path, subpath=subpath)
    scope = f" [subpath={subpath}]" if subpath else ""
    print(f"enrich ok: {stats} (notes model={settings.notes_model}){scope}")
    return 0


def rollup(repo_path: str) -> int:
    from .indexer import rollup_repo

    stats = rollup_repo(repo_path)
    print(f"rollup ok: {stats} (rollup model={settings.rollup_model})")
    return 0


def ingest(docs_path: str, repo: str | None = None) -> int:
    from .indexer import ingest_docs

    stats = ingest_docs(docs_path, repo)
    print(f"ingest ok: {stats}")
    return 0


def link(repo: str) -> int:
    from .indexer import link_docs

    stats = link_docs(repo)
    print(f"link ok: {stats}")
    return 0


def confluence_sync(space: str, dest_path: str, repo: str | None = None) -> int:
    from . import confluence
    from .indexer import ingest_docs, link_docs

    stats = confluence.sync_space(space, dest_path)
    print(f"confluence-sync ok: {stats} -> {dest_path}")
    if repo is None:
        # Sync and ingest stay separable: a corpus on disk is inspectable before anything is
        # embedded, and that is the whole point of keeping the archive as Layer 1.
        print(f"   next: python -m code_context.dev ingest {dest_path} <repo>   # then: link <repo>")
        return 0
    print(f"ingest: {ingest_docs(dest_path, repo)}")
    print(f"link: {link_docs(repo)}")
    return 0


def agents_md(repo_path: str, *flags: str) -> int:
    from . import agents_md as _agents_md

    force = "--force" in flags
    result = _agents_md.write_starter(repo_path, force=force)
    if not result["written"]:
        print(f"agents-md: {result['path']} already exists — left alone (--force to replace)")
        return 0
    print(f"agents-md ok: wrote {result['path']} ({result['modules']} areas mapped) — fill in the TODOs")
    return 0


def search(*query: str) -> int:
    for f in tools.search_code(" ".join(query), limit=8):
        print(f"{f['path']}:{f['line_start']}  {f['kind']} {f['symbol']}")
    return 0


def usages(symbol: str) -> int:
    for u in tools.find_usages(symbol):
        print(f"{u['path']}:{u['line']}  {u['symbol']}")
    return 0


def deps(symbol: str) -> int:
    for f in tools.get_deps(symbol):
        print(f"{f['path']}:{f['line_start']}  {f['kind']} {f['symbol']}")
    return 0


COMMANDS = {
    "db-ping": db_ping,
    "migrate": migrate,
    "embed-smoke": embed_smoke,
    "index": index,
    "enrich": enrich,
    "rollup": rollup,
    "ingest": ingest,
    "link": link,
    "confluence-sync": confluence_sync,
    "agents-md": agents_md,
    "search": search,
    "usages": usages,
    "deps": deps,
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: python -m code_context.dev {{{'|'.join(COMMANDS)}}} [args]", file=sys.stderr)
        return 2
    obs.setup()
    command, args = argv[0], argv[1:]
    try:
        # Events go to stderr; the human summary each command prints stays on stdout, so a run
        # stays readable while its event stream is redirected independently.
        with obs.timed("run", command=command, target=args[0] if args else None):
            return COMMANDS[command](*args)
    except TypeError:
        print(f"usage: python -m code_context.dev {command} <arg>", file=sys.stderr)
        return 2
    finally:
        # An `enrich`/`rollup` run is exactly the long occupation of the shared engine the C-6a
        # handshake exists for, so hand it back at the end of the command rather than waiting out
        # the idle TTL. A no-op unless the lifecycle flag is on and something was acquired;
        # lifecycle's own atexit hook covers entry points that never reach here.
        try:
            lifecycle.release("stop")
        except Exception as exc:
            # Never let the handback mask the command's own outcome — a raise in `finally`
            # replaces the real error. It is already an ERROR event; this is the human line.
            print(f"lifecycle: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
