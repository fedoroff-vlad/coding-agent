# coding-agent

Local-first coding-agent contour that shares one inference engine (32B Qwen-Coder class,
resident) with the [ai-life](https://github.com/fedoroff-vlad/ai-life) household assistant.
Runs on demand (`docker compose up` for a coding session, `down` after), so the two projects
co-exist on a single Mac Studio without holding two large models in memory at once.

**Core idea — RAG-first, not a giant context window.** A `code-context` MCP server gives the
agent *hands in the codebase* (`search_code` / `get_file` / `find_usages` / `get_deps`) and in the
project's documentation (`search_docs` / `find_convention`) over pgvector, so every LLM call sees
only ~8–20k of relevant context instead of the whole repo. This is what makes the plan viable on
64 GB.

## Quickstart

> **What works today:** the dev environment + a working **Java indexer** (tree-sitter → embeddings →
> pgvector + a relation graph) with **all six MCP tools live** — `search_code` / `get_file` /
> `find_usages` / `get_deps` over code, `search_docs` / `find_convention` over an ingested docs
> corpus — plus **LLM notes**: `enrich` annotates each non-trivial class with a semantic note, and
> `rollup` synthesizes those bottom-up into directory/module/project notes (md-as-source →
> retrievable fragments). Agent-shell integration is next (see [Status](#status)) — you can't drive
> a full coding session yet, but you can index a Java repo, ingest its Confluence export, enrich +
> roll it up, and semantically search + navigate both.

From a fresh clone:

1. **Install everything.** macOS: `./scripts/bootstrap-mac.sh` · Windows: `.\scripts\bootstrap-win.ps1`
   (toolchain + apps + Python 3.13 via uv + models). See [Set up a new machine](#set-up-a-new-machine).
2. **Launch a dev session.** macOS: `./scripts/start-mac.sh` · Windows: `.\scripts\start-win.ps1`
   (Ollama + dev pgvector on :5433 + uv env + `code.*` schema applied).
3. **Index a Java repo and search it:**
   ```sh
   uv run python -m code_context.dev index /path/to/java-repo       # tree-sitter → embed → pgvector
   uv run python -m code_context.dev enrich /path/to/java-repo      # LLM leaf notes (needs a notes model)
   uv run python -m code_context.dev rollup /path/to/java-repo      # bottom-up dir/module/project notes
   uv run python -m code_context.dev ingest /path/to/docs-export my-repo   # HTML/.docx/.pdf → docs
   uv run python -m code_context.dev link my-repo                   # doc → class 'mentions' edges
   uv run python -m code_context.dev search "where do we recall memories by vector"
   ```
   Then run the MCP server with `uv run code-context` — all six tools are live.

   Three things worth knowing before the first real run:

   - **Scope your queries.** Every tool takes a `repo` argument and falls back to
     `CODE_CONTEXT_DEFAULT_REPO`. Set it as soon as the index holds more than one project, or
     queries will mix them.
   - **`enrich` / `rollup` need a model** — `CODE_CONTEXT_NOTES_MODEL` / `CODE_CONTEXT_ROLLUP_MODEL`
     (e.g. `qwen3:8b` on the dev box; the prod default is the resident `qwen3-coder:30b`). Run
     `rollup` after `enrich`.
   - **`ingest` needs no model at all** (parse → embed → link), so the docs half runs comfortably on
     a CPU-only box. Pass the **code repo's name** as its second argument so docs and code share one
     scope — that is what lets `link` connect a rule to the class it governs. `ingest` links as its
     last step; re-run `link` on its own after a code re-index.

## Where to start

- **[CLAUDE.md](CLAUDE.md)** — session reading order + conventions (for developing this repo).
- **[plans/INDEX.md](plans/INDEX.md)** — map of the planning docs.
- **[plans/REFERENCE.md](plans/REFERENCE.md)** — source of truth: strategy, quality expectations
  (three-tier local/cheap-API/flagship), the RAG-first decision, the `code-context` MCP contract.
- **[plans/roadmap.md](plans/roadmap.md)** — how it's built: the universal **Step 0** onboarding
  pipeline, the SDD work cycle, build phases C-0…C-9, and the locked decisions.
- **[plans/STATUS.md](plans/STATUS.md)** — what's in flight / the next slice.

## Status

**Phases C-0…C-4 shipped; C-3 (docs) closed; the ai-life lifecycle signal (C-6a) built. Next: the
onboarding kit (C-5) and shell integration (C-6).** Foundation + working indexer (facts + graph) + LLM notes (leaf + rollups) + the docs corpus.
The `code-context` MCP
server (`src/code_context/`), the derived-index schema (`code.*` — pgvector `fragment` + relation
`edge`), a **Java indexer** (tree-sitter chunk → nomic embeddings → pgvector + call/import/contains
edges) with `search_code` / `get_file` / `find_usages` / `get_deps`, a **docs corpus** (Confluence
HTML export → sections → `doc` fragments + doc↔code `mentions` edges) behind `search_docs` /
`find_convention`, and the **LLM-notes** enrichment —
per-class **leaf notes** (C-4a) plus bottom-up **directory→module→project rollups** (C-4b), all
md-as-source → retrievable fragments (trivial data carriers skipped). Proven on ai-life
`platform/memory-service` (202 fragments, 1168 edges) and on a 143-file production repo. **AGE decided
against on data** — every graph tool is a 1-hop join, so plain `code.edge` suffices.

**Sharing the Mac with ai-life (C-6a).** Both contours use one Ollama, and the GPU working set tops
out near 48 GB, so before this repo loads its analyzer model ai-life must have *finished*
downshifting its own — and on the way back we unload ours, confirm it is gone, and only then let
ai-life restore. That handshake ships as `src/code_context/lifecycle.py`, **opt-in and OFF by
default** (`CODE_CONTEXT_LIFECYCLE_ENABLED=true` + `CODE_CONTEXT_LIFECYCLE_GATEWAY_URL`): with the
flag off this repo never talks to ai-life at all, and either project runs standalone. It is the
caller of ai-life's `/v1/model-profile` (its slice LC-4) — turn it on only once that endpoint exists,
since an unconfirmed downshift deliberately **fails** the run instead of loading over the ceiling.

**What is deliberately not built yet:** the agent shell (C-6 — Aider/Continue/Cline still to be
chosen), the Step-0 onboarding kit (C-5), the authored `AGENTS.md`/OpenSpec layer (C-7), and the Java
sidecar that would replace simple-name matching (C-8).
Inside shipped phases one thing stays open: the docs front reads an exported corpus (HTML, `.docx`
and text-layer `.pdf`; OCR for scans and Confluence REST sync deferred) rather than syncing
Confluence itself. Rollup escalation now ships — prefix a model with `anthropic:`
(`CODE_CONTEXT_ROLLUP_MODEL=anthropic:claude-opus-4-8` + `uv sync --extra cloud`) and that tier runs
on the Messages API instead of Ollama; the defaults stay local, since escalation costs per directory.
Phase-by-phase state: [`plans/roadmap.md`](plans/roadmap.md); slice-level detail:
[`plans/STATUS.md`](plans/STATUS.md).

## Layout

- `src/code_context/` — the MCP server + indexer (see [architecture.md](architecture.md)).
- `pyproject.toml` — Python core (`mcp`, `psycopg`, `pydantic-settings`); `[index]` extra adds
  tree-sitter from C-2, `[docs]` adds the HTML parser + the `.docx`/`.pdf`→markdown converters for
  the docs ingest (C-3), `[cloud]` adds the
  Anthropic SDK for the escalated analyzer tier (C-4, opt-in — the local path never imports it).
- `plans/` — strategy, roadmap, status (read [CLAUDE.md](CLAUDE.md) first).
- `src/code_context/migrations/` — raw-SQL schema migrations + [their own README](src/code_context/migrations/README.md)
  (the one rule that bites: a committed migration is never edited, comments included).
- `infra/` — the dev pgvector container + the backup sidecar · `scripts/` — bootstrap, dev session,
  model pulls, the golden runner, and the drift-lint CI step.

## Set up a new machine

Clone the repo, then one command installs everything (package manager + tools + apps + Python env + models).
Idempotent and declarative — the toolset lives in [`Brewfile`](Brewfile) (macOS) / [`winget-packages.json`](winget-packages.json)
(Windows), the models in `scripts/pull-models.*`. `SKIP_MODELS=1` (macOS) / `$env:SKIP_MODELS='1'` (Windows)
installs tools only.

**macOS** (Homebrew):

```sh
./scripts/bootstrap-mac.sh    # install everything
./scripts/start-mac.sh        # launch a dev session
```

**Windows** (winget):

```powershell
.\scripts\bootstrap-win.ps1   # install everything
.\scripts\start-win.ps1       # launch a dev session
```

A dev session = ollama + dev pgvector + uv env + schema, ready for `uv run code-context`.

## Develop

Tooling is [**uv**](https://docs.astral.sh/uv/) (Python 3.13, pinned in `.python-version`; the exact
dependency set is locked in `uv.lock`). uv provisions the interpreter itself — no manual Python install.

```sh
uv sync --extra dev --extra index --extra docs --extra cloud   # create .venv from the lockfile
uv run ruff check .                  # lint
uv run pytest -q                     # tests
uv run code-context                  # run the MCP server (stdio)
```

### Logs

Every run emits **JSON Lines on stderr**, one self-contained event per line — ready to ship to
Elasticsearch/Kibana with a collector, no grok pattern (contract: [`architecture.md`](architecture.md)
§Observability). stdout stays free: it is the MCP protocol channel, and it carries the dev CLI's
human summaries, so you can redirect the two independently.

```sh
CODE_CONTEXT_LOG_LEVEL=DEBUG uv run python -m code_context.dev enrich /path/to/repo 2>run.jsonl
CODE_CONTEXT_LOG_FORMAT=text uv run python -m code_context.dev rollup /path/to/repo   # human-readable
```

```json
{"@timestamp":"…","log.level":"debug","event.action":"enrich.note","run_id":"72f642d9cf88",
 "input":"DubService.java","symbol":"DubService","output":"DubService.md","outcome":"ok","event.duration":4210}
```

`INFO` (default) gives run start/finish, counts and warnings; `DEBUG` adds a line per class/directory
with its timing. **Payloads are never logged at any level** — prompts, class bodies and note text are
customer source code, and these events are meant to leave the machine. Events carry names, sizes and
durations; the content itself is on disk under `.code-context/notes/`. Watch for
`llm.context_pressure`: it warns when a prompt crowds `num_ctx`, the one failure mode that is
otherwise silent (the engine just drops the overflow).

### Dev database (isolated pgvector)

An experimental index runs against a throwaway pgvector container, separate from ai-life's DB:

```sh
docker compose -f infra/docker-compose.yml up -d        # pgvector on host :5433
uv run python -m code_context.dev db-ping               # reach Postgres
uv run python -m code_context.dev migrate               # apply DB migrations (idempotent)
uv run python -m code_context.dev embed-smoke           # Ollama embeds at the configured dim
docker compose -f infra/docker-compose.yml down -v      # tear down + wipe the volume
```

`embed-smoke` needs Ollama with the embed model pulled (`ollama pull nomic-embed-text`).

The compose also runs a `db-backup` sidecar (OSS `postgres-backup-local`) — a daily `pg_dump` +
gzip into `infra/backups/`, rotated 7 daily + 4 weekly (mirrors ai-life's backup sidecar). This
index is **derived and rebuildable** (`dev index` + `dev enrich` regenerate it) and in prod it lives
in ai-life's already-backed-up Postgres, so the sidecar is really for a **standalone** deployment;
in the throwaway dev flow (`down -v`) it's just a convenience. Restore a dump:

```sh
gunzip -c infra/backups/last/code_context-latest.sql.gz \
  | docker exec -i code-context-db psql -U dev -d code_context
```

### Golden lane (retrieval quality)

Opt-in tests that check retrieval against a **real** Ollama + pgvector (excluded from the unit run):

```sh
./scripts/golden.sh          # macOS/Linux   ·   .\scripts\golden.ps1 on Windows
uv run pytest -m golden      # if the DB + Ollama are already up
```

Four lanes over a tiny fixture repo (distinct auth / math / billing classes):

- **retrieval** — the right class comes back, edges resolve;
- **notes** and **rollup** — drive the real `enrich` / `rollup` passes and assert the right classes
  get leaf notes (data carriers don't) and the right dir/module/project tiers get rollups;
- **docs** — the whole C-3 path: retrieval by meaning, provenance on every result, a doc↔code link
  surfacing through `find_convention`, and two repos in one index proving the scoping.

The docs lane drives **no analyzer model** (docs ingest is parse → embed → link), so it is cheap even
on a CPU-only box — run it alone with `./scripts/golden.sh tests/test_golden_docs.py`. The notes and
rollup lanes do need one: set `CODE_CONTEXT_NOTES_MODEL` / `CODE_CONTEXT_ROLLUP_MODEL` (default
`qwen3:8b` on the dev box). All lanes run on a clean slate, so they leave the dev DB holding only the
fixture (the index is rebuildable — re-`index` your repo afterwards).

## License

[MIT](LICENSE) — do what you like; keep the copyright notice.
