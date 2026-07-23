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
> a full coding session yet, but you can index a Java repo, sync its Confluence space over the REST
> API, enrich + roll it up, and semantically search + navigate both.

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
   uv run python -m code_context.dev agents-md /path/to/java-repo    # starter AGENTS.md in THAT repo
   ```
   Then run the MCP server with `uv run code-context` — all six tools are live.

   Three things worth knowing before the first real run:

   - **Scope your queries.** Every tool takes a `repo` argument and falls back to
     `CODE_CONTEXT_DEFAULT_REPO` — the repo's **name** (the indexed directory's leaf name), never
     its path. Set it as soon as the index holds more than one project, or queries will mix them.
   - **`enrich` / `rollup` need a model** — `CODE_CONTEXT_NOTES_MODEL` / `CODE_CONTEXT_ROLLUP_MODEL`
     (e.g. `qwen3:8b` on the dev box; the prod default is the resident `qwen3-coder:30b`). Run
     `rollup` after `enrich`. Notes are built from **signatures only** by default; for a repo you
     trust as much as your own working tree, `CODE_CONTEXT_NOTES_INCLUDE_BODIES=1` feeds method
     bodies and declared fields for richer notes (relaxes the signatures-only invariant — architecture.md §Security).
     `enrich` takes an optional subpath — `dev enrich <repo> <module/dir>` — to scope a trial to one
     module before paying for the whole repo (the repo scope stays the root, pruning only its files).
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
caller of ai-life's `/v1/model-profile` (its slice LC-4, shipped 2026-07-21), so **both halves now
exist** — enable the two flags together, because ai-life only steps down when asked and an
unconfirmed downshift deliberately **fails** the run instead of loading over the ceiling.

**The shell is [opencode](https://opencode.ai)** (C-6, decided 2026-07-21) — reused, not built. This
repo is its MCP server; `scripts/work-win.ps1` registers it and installs the skills.

**What is deliberately not built yet:** the Step-0 onboarding kit (C-5), the authored
`AGENTS.md`/OpenSpec layer (C-7), and the Java sidecar that would replace simple-name matching (C-8).
Inside shipped phases one thing stays open: **OCR for scanned PDFs** — the docs front reads an
exported corpus (HTML, `.docx`, text-layer `.pdf`) *and* now syncs a Confluence space over the REST
API (decision B, incremental on `version.number`), but a scan is reported rather than ingested blank. Analyzer escalation now ships as a config change — the model string picks the engine: `anthropic:…`
runs on the Messages API (`uv sync --extra cloud`), `openai:…` on an **OpenAI-dialect company
gateway** (`CODE_CONTEXT_OPENAI_BASE_URL`, no extra needed — see §Use it on a work machine), and a
bare tag stays on local Ollama, which is still the default.
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
  the [refresh helper](scripts/restart-win.ps1) (restart containers + re-sync + migrate after a pull; macOS `restart-mac.sh`),
  the [work-machine quickstart](scripts/work-win.ps1) (infra + index + opencode wiring),
  model pulls, the golden runner, and the drift-lint CI step.
- `tools/agent-skills/` — submodule of the portable dev-workflow skills; `work-win.ps1` installs
  them into opencode, since it only reads `SKILL.md` from six fixed locations.

## Set up a new machine

Clone the repo, then one command installs everything (package manager + tools + Python env + models).
Idempotent and declarative — the toolset lives in [`Brewfile`](Brewfile) (macOS) / [`winget-packages.json`](winget-packages.json)
(Windows), the models in `scripts/pull-models.*`. `SKIP_MODELS=1` (macOS) / `$env:SKIP_MODELS='1'` (Windows)
installs tools only.

The macOS Brewfile is the owner's full workstation (it also brings personal apps); the Windows set is
**coding-agent essentials only** (git, gh, uv, ollama, docker, opencode, Claude Code). Windows also indexes
rather than runs the coder, so `bootstrap-win.ps1` pulls only the embedding model by default — `$env:PULL_CODER='1'`
adds the ~19 GB local coder.

**macOS** (Homebrew):

```sh
./scripts/bootstrap-mac.sh    # install everything
./scripts/start-mac.sh        # launch a dev session
```

**Windows** (winget):

```powershell
.\scripts\bootstrap-win.ps1   # coding-agent tools + embedding model (coder runs on the Mac)
.\scripts\start-win.ps1       # launch a dev session
```

A dev session = ollama + dev pgvector + uv env + schema, ready for `uv run code-context`.

After a `git pull` that brings new migrations, dependencies or a changed compose file, refresh the
running environment without a full re-bootstrap: `.\scripts\restart-win.ps1` (macOS:
`./scripts/restart-mac.sh`) restarts the containers, re-syncs the env and re-applies migrations
(`-Clean` / `--clean` wipes the DB volume for a fresh index; `-Reindex` / `--reindex <repo>`
re-indexes after). It refreshes the infrastructure, not opencode's MCP child — a change to the
server code is picked up by restarting opencode.

## Use it on a work machine (a company LLM + your own shell)

The shell is not ours (C-6 — reuse, don't rebuild), so the setup is: **your shell holds the company
model; this repo gives that shell hands in the codebase over MCP.** Two facts make this cheap:

- **Retrieval needs no LLM at all.** `index`, `search_code`, `get_file`, `find_usages`, `get_deps`
  and the whole docs ingest use *embeddings only*. You can be productive before any analyzer model
  is configured — the semantic notes (`enrich` / `rollup`) are an upgrade, not a prerequisite.
- **Embeddings stay local** (`nomic-embed-text`, 274 MB, fine on CPU). Swapping the embed model to
  a remote one changes `embed_dim` *and* the `vector(N)` column, i.e. a migration and a full
  re-index — not a price worth paying to get started.

### From a clone to a working shell

The work machine is somebody else's Windows box, so start from zero:

```powershell
git clone --recurse-submodules https://github.com/fedoroff-vlad/coding-agent
cd coding-agent
.\scripts\work-win.ps1                    # asks which repository to index
```

Two prerequisites the script installs *neither* of, because both want a reboot or an administrator
on a managed machine — it checks for them first and names them rather than failing later:

```powershell
winget install --id Docker.DockerDesktop   # the pgvector container runs on it
winget install --id Ollama.Ollama          # local embeddings (no analyzer model needed)
```

Everything else it does handle: `uv` and the Python env, the database and its migrations, the embed
model, opencode itself, the MCP registration and the skills. If you cloned without
`--recurse-submodules` it initialises the submodule for you.

**Before you index a work repository, read [`scrub-identity`](tools/agent-skills/skills/scrub-identity/SKILL.md)
and fill in `.private-terms`.** This repo is public. Indexing writes markdown notes into the target
repo (`.code-context/notes/`), and an internal hostname or a service name in a commit, doc or
fixture identifies an employer as surely as a name does. This repository already had to be
recreated over exactly that — GitHub serves `refs/pull/<N>/head` forever, so no force-push undoes it.
**Neither half of that guard survives a clone** — the terms file is gitignored (a published denylist
is the leak, with an index attached) and the hook lives in `.git/hooks/`. So the script installs the
hook and **writes a `.private-terms` template** with the categories spelled out — employer names in
every spelling, internal domains, internal service names, Confluence space keys, industry
vocabulary — for you to fill in. Every line in it is a comment, so it protects nothing until you
edit it, and the script keeps saying so on each run; commits stay refused meanwhile, which is the
intended state. Carry the real terms between machines out of band.

**The one command in full** ([`scripts/work-win.ps1`](scripts/work-win.ps1)) — dev session (Docker, uv,
Ollama, pgvector, migrations) → the embed model → index your repo → install opencode → point it at
your gateway → register the MCP server in it → install the dev-workflow skills. Idempotent;
re-running is also how you refresh the skills after a submodule bump.

```powershell
.\scripts\work-win.ps1 -Repo C:\path\to\your\monorepo -GatewayUrl https://<gateway>/v1 -Model <model-id>
.\scripts\work-win.ps1 -Repo C:\path\to\repo -SkipIndex   # everything but the slow indexing
.\scripts\work-win.ps1 -WireOnly                          # just opencode: re-register + refresh skills
```

It pulls **only `nomic-embed-text`** (~274 MB), not the analyzer models — a machine driving a
company gateway needs none of them. It also **merges** into an existing `opencode.json` (backing it
up first) rather than overwriting a provider config that is already there; if you keep an
`opencode.jsonc`, it prints the entries for you to paste instead, because an automatic rewrite
would delete your comments.

### The gateway: what the shell thinks with

Two different things reach that gateway, and it is worth keeping them apart:

| consumer | what it is for | how it is configured |
|---|---|---|
| **opencode** | the model the *agent* reasons with — every prompt you type | a `provider` block in `opencode.json` (below) |
| **this repo's analyzer** | the optional `enrich` / `rollup` notes written at index time | `CODE_CONTEXT_OPENAI_*` + an `openai:`-prefixed model |

Same URL, same key, two processes. `-GatewayUrl … -Model …` writes the provider half:

```json
{
  "provider": {
    "work-gateway": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "https://<gateway>/v1", "apiKey": "{env:CODE_CONTEXT_OPENAI_API_KEY}" },
      "models": { "<model-id>": { "name": "<model-id>" } }
    }
  },
  "model": "work-gateway/<model-id>"
}
```

**The key is never a script parameter and never a literal in that file** — a secret on a command
line lands in PSReadLine history and in the process list, and the config file is world-readable on
the box and rides along in backups. opencode resolves `{env:…}` at start, so the value lives in
your environment only:

```powershell
setx CODE_CONTEXT_OPENAI_API_KEY "<your-key>"    # future shells
$env:CODE_CONTEXT_OPENAI_API_KEY = "<your-key>"  # this one
```

The base URL must carry the `/v1` (the script warns if it does not — without it the first call
404s far from the cause), and neither the URL nor the key belongs in a commit: **this repo is
public and an internal hostname identifies an employer as surely as a name does.**

The same mechanism is how the **Mac** profile works — a second provider pointing at local Ollama
(`http://localhost:11434/v1`, any key), switched with `/models`. One config, two providers.

Windows only, deliberately — that is the work machine. The portable form is the same steps by hand:

```sh
docker compose -f infra/docker-compose.yml up -d      # pgvector on :5433
uv sync --extra dev --extra index --extra docs
uv run python -m code_context.dev migrate
ollama pull nomic-embed-text
uv run python -m code_context.dev index /path/to/your/repo
uv run python -m code_context.dev search "where is the retry policy"
```

plus opencode itself (`winget install SST.opencode`, `brew install sst/tap/opencode`, or the
installer from [opencode.ai](https://opencode.ai)), the `provider` block above, and this in
`~/.config/opencode/opencode.json` — the shell then has `search_code` / `get_file` / `find_usages` /
`get_deps` / `search_docs` / `find_convention`, a narrow slice of the codebase on demand instead of
the whole repo in its window, which is the entire point:

```json
{
  "mcp": {
    "code-context": {
      "type": "local",
      "command": ["uv", "run", "--directory", "/path/to/coding-agent", "code-context"],
      "environment": {
        "CODE_CONTEXT_DEFAULT_REPO": "your-repo",
        "CODE_CONTEXT_DB_DSN": "postgresql://dev:dev@localhost:5433/code_context"
      }
    }
  }
}
```

`CODE_CONTEXT_DEFAULT_REPO` matters: one index can hold several repos, and an unscoped query mixes
them — a wrong answer rather than a wide one. **Its value is the repo's *name*, not its path**:
`dev index C:\src\my-monorepo` stores the fragments under `my-monorepo`, and the scope filter is an
exact string compare — a path there matches no row, so every tool returns nothing while the setup
looks complete. (That is not hypothetical: this file said `/path/to/your/repo` until a live check
returned 0 results for the path and 10 for the name.)

**Confluence.** The wiki syncs over the REST API — no manual export:

```sh
export CODE_CONTEXT_CONFLUENCE_BASE_URL=https://<your-wiki>          # Cloud: https://<site>.atlassian.net
export CODE_CONTEXT_CONFLUENCE_API_PATH=rest/api                     # Cloud: wiki/rest/api
export CODE_CONTEXT_CONFLUENCE_TOKEN=...                             # env only, never a file in the repo
# export CODE_CONTEXT_CONFLUENCE_EMAIL=you@example.com               # Cloud only — switches to Basic
uv run python -m code_context.dev confluence-sync SPACEKEY ./wiki-corpus my-repo
```

It **syncs to disk and then ingests**: each page is written as HTML into the corpus directory and
the existing docs pass runs over it, so the archive stays the greppable, diffable Layer-1 record the
notes and `.docx`/`.pdf` passes already keep — and a failed sync cannot corrupt the index. Drop the
last argument to sync only and inspect the corpus before anything is embedded.

Re-runs are incremental on Confluence's own `version.number`; renamed pages keep one file (the id
leads the filename), deleted pages are removed from the corpus. Pass the **code repo's name** as the
last argument so docs and code share a scope — that is what lets `find_convention` answer "which
rule governs this class". Auth follows the edition: a Data Center **Personal Access Token** is sent
as `Bearer`, and setting the email switches to Cloud's Basic (email + API token).

**Give the shell its instructions.** Wiring the tools is not the same as getting them used: with no
rules file, opencode falls back to opening and grepping files — the whole-repo-in-the-window habit
this project exists to replace. `AGENTS.md` in the **target** repository is what changes that, and
`agents-md` writes a starter:

```sh
uv run python -m code_context.dev agents-md /path/to/your/repo   # --force to replace
```

It generates the half a machine can know — a retrieval protocol naming the six tools, and a map of
the repo's top-level areas taken *from the index* — and leaves every convention as an explicit
`TODO`. It invents nothing: a plausible-sounding rule that no human wrote is authoritative-looking
text an agent will follow. An existing `AGENTS.md` is never overwritten without `--force`.

Two facts worth knowing: opencode reads the project's `AGENTS.md` (falling back to `CLAUDE.md`) and
a global `~/.config/opencode/AGENTS.md`, but it does **not** auto-discover nested files in
subdirectories — a monorepo hierarchy needs an explicit glob, e.g. `"instructions": ["*/AGENTS.md"]`
in `opencode.json`.

**Skills.** opencode discovers `SKILL.md` only in six fixed locations, and a submodule under
`tools/` is not one of them, so the skills in [`tools/agent-skills/`](tools/agent-skills) have to be
*installed*: copy each `skills/<name>/` into `~/.config/opencode/skills/` (what the script does).
Global rather than `.opencode/skills/` inside the work repo — they are your workflow, not that
repository's, and they must not show up in its diff.

**Semantic notes through the gateway (optional, later):**

```sh
export CODE_CONTEXT_OPENAI_BASE_URL=https://<your-gateway>/v1   # keep this out of git
export CODE_CONTEXT_OPENAI_API_KEY=...                          # env only, never a file in the repo
export CODE_CONTEXT_NOTES_MODEL=openai:<model-the-gateway-exposes>
uv run python -m code_context.dev enrich /path/to/your/repo
```

**Analyzer tiers, for reference.** The model string picks the engine: a bare tag → local Ollama,
`anthropic:…` → the Messages API (needs `--extra cloud`), `openai:…` → the OpenAI-dialect gateway
above (no extra needed). Only the local tier signals the ai-life lifecycle handshake, because only
it loads a model onto the shared machine.

You can smoke-test the `openai:` path with no gateway at all — Ollama speaks the same dialect:

```sh
CODE_CONTEXT_OPENAI_BASE_URL=http://localhost:11434/v1 \
  uv run python -c "from code_context import llm; print(llm.generate('say PONG', model='openai:qwen3:8b'))"
```

If your gateway fronts a *thinking* model, set `CODE_CONTEXT_OPENAI_SUPPRESS_THINKING=true` — a
note is two sentences and the reasoning pass otherwise dominates it (measured on the command above:
33.5 s → 3.7 s). It is off by default because a gateway that rejects unknown body fields would fail
every call.

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
