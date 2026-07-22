# architecture — coding-agent

Component-level view of the built system. The **why** (strategy, RAG-first, quality tiers, the
two-model swap) lives in [`plans/REFERENCE.md`](plans/REFERENCE.md) — this file is the **what/where**
of the code, split out in phase C-0 now that real structure exists.

## Contours

Two contours share one Mac + one Ollama engine, never two 32B resident at once (REFERENCE §1):

- **ai-life** (sibling repo) — the 24/7 household assistant on `qwen3:32b`.
- **coding-agent** (this repo) — the on-demand coding contour on `qwen3-coder:30b` (Qwen3-Coder-Flash).

The model *switch* and hot/cold supervision live on the ai-life side (`../ai-life/plans/lifecycle.md`,
slice LC-4). **Emitting the signal is ours — slice C-6a, built** (`lifecycle.py`), and it is not a
footnote: on start we signal `coder-active` **before loading the coder model** and wait for ai-life's
confirmed downshift; on stop / idle-timeout we unload our model, confirm it is gone, then signal
`normal`. Loading first and signalling after would put ~39 GB of models resident at once and bust the
~48 GB ceiling. The whole coupling is **opt-in (default OFF)** and model tags come from env — either
contour must run standalone unaffected.

**As built (C-6a).** `lifecycle.acquire(model)` sits immediately before the local analyzer POST in
`llm.py` — that POST *is* the load, so it is the last point at which the ordering can be honoured —
and `lifecycle.release(reason)` runs at the end of a dev-CLI command, on an idle TTL, or from an
`atexit` backstop. The wire is one endpoint, ai-life's `/v1/model-profile`, carrying
`{"profile": "coder-active"|"normal"}`. Four properties are deliberate:

- **A failed handshake fails the run.** No confirmation from ai-life (refusal, unreachable gateway,
  or a 404 because *its* flag is off) raises `LifecycleError` and nothing is loaded — "carry on
  anyway" is precisely the over-budget load. The mirror case, failing to *restore* ai-life
  afterwards, leaves the box under budget, so it is logged (`restored=false`) rather than raised.
- **The unload is confirmed, not assumed.** `keep_alive: 0` returns before the memory is actually
  freed, so `/api/ps` is polled until the model is gone; if it never goes, `normal` is **not** sent.
- **Only the analyzer is gated.** Embeddings (`nomic-embed-text`, a few hundred MB) do not move the
  ceiling and gating them would make every `index` run wait on ai-life; the `anthropic:` cloud tier
  loads nothing locally, so it never signals either.
- **The session, not the call, is the unit.** One signal covers a whole enrich/rollup pass; the idle
  TTL hands the engine back when the pass ends, and the next analyzer call re-acquires.

## The product: `code-context` MCP + indexer

RAG-first — the agent gets *hands in the codebase* instead of the whole repo in its window. Three
context layers (REFERENCE §3, roadmap §Architecture):

1. **Spec/doc (authored, in-repo):** `AGENTS.md` (hierarchical) + `architecture.md` + OpenSpec —
   thin, authoritative, always loaded. It lives in the **target** repository and is read by the
   shell directly; it never travels through this index. `agents_md.py` writes a *starter* — the
   retrieval protocol plus a map derived from the index — and leaves every convention a `TODO`,
   because an invented rule is authoritative-looking text an agent will follow. **Hierarchy is not
   automatic:** opencode reads the project root's file and a global one, but discovers no nested
   `AGENTS.md`, so a monorepo needs an explicit `"instructions": ["*/AGENTS.md"]` glob in
   `opencode.json`.
2. **RAG (machine-built — this component):** code + docs → a derived pgvector + edge-graph index,
   pulled as a narrow slice on demand.
3. **Shell (reused):** **opencode** drives the loop — decision D, closed 2026-07-21 (roadmap
   §Open decisions). It is registered as an MCP client of this server, and it reaches the model it
   thinks with through an `@ai-sdk/openai-compatible` **provider** block — the same OpenAI-dialect
   gateway `llm.py`'s `openai:` tier calls, so one URL and one key serve both, and the key is a
   `{env:…}` reference rather than a literal in either. Both halves are written by
   [`scripts/work-win.ps1`](scripts/work-win.ps1); see README §Use it on a work machine.

### Data flow (current)

```
  [ Agent shell + LLM ]                        reused, not built (C-6)
          │  MCP (stdio)
          ▼
  ┌──────────────────────────────────────────────┐
  │  code-context MCP server                       │
  │  ✅ search_code  get_file  find_usages  get_deps│
  │  ◐  find_convention  search_docs   (docs → C-3) │
  └───────────────┬────────────────┬───────────────┘
       Indexer ───┘                └─── Retrieval
   chunk+edges · notes · rollup   vector + 1-hop graph
   (repo *.java → in)                │
          │ write            read ◄──┘         ┌──────────────────────────┐
          ▼                                    │ Ollama (shared w/ ai-life)│
  ┌──────────────────────────┐   ──embed──────►│ nomic-embed-text (768)    │
  │ Postgres  code.*          │   ──enrich─────►│ analyzer (notes model)    │
  │ fragment (pgvector)       │   ──rollup─────►│ rollup tier (→ Opus later)│
  │  facts · leaf notes ·     │                └──────────────────────────┘
  │  dir/module/project notes │       md notes (Layer 1, md-as-source)
  │ edge (1-hop graph, no AGE)│  ◄──── .code-context/notes/**.md ────► git
  │ migrations: yoyo          │
  └──────────────────────────┘
```

### Status at a glance

| Area | State |
|---|---|
| Java indexer (chunk + call/import/contains edges), incremental | ✅ built |
| `search_code` · `get_file` · `find_usages` · `get_deps` | ✅ built |
| `code.*` schema + yoyo migrations · embeddings · dev infra · golden lane · CI · bootstrap (mac/win) | ✅ built |
| Docs ingest: HTML / `.docx` / `.pdf` → sections → `doc` fragments, incremental (D-1/D-2/D-6/D-7) + doc↔code `mentions` edges (D-3) | ✅ built (C-3) |
| `find_convention` · `search_docs` (docs corpus, provenance in the shape) · repo-scoped retrieval | ✅ built (C-3 / D-4) |
| LLM **leaf** notes (per class, md-as-source → `note` fragment; trivial classes skipped) | ✅ built (C-4a) |
| LLM note **rollups** (bottom-up directory→module→project, md-as-source → tier fragments) | ✅ built (C-4b) |
| Lifecycle signal to ai-life (`coder-active` handshake + confirmed unload + idle TTL), opt-in | ✅ built (C-6a) |
| Authored layer: `AGENTS.md` **starter generator** (`agents_md.py` + `dev agents-md`) | ✅ built (C-7, first slice) |
| Authored layer: OpenSpec cycle · Java sidecar (type resolution) | ○ planned → C-7 / C-8 |

### Package layout (`src/code_context/`)

| Module | Role |
|---|---|
| `config.py` | Env-driven settings (`CODE_CONTEXT_*`): Postgres DSN, Ollama URL, embed model/dim. |
| `server.py` | MCP entry point (stdio). Thin router — wraps the tool contracts, no logic. |
| `tools.py` | The 6 tool **contracts** + return shapes, all implemented: `search_code` (pgvector cosine, docs excluded), `get_file`, `find_usages` (call sites), `get_deps` (imported classes), `search_docs` (the docs corpus, returned as `Doc` rows with `source`/`trust`), `find_convention` (docs ranked for the class you are writing — `mentions`-linked sections first, semantic hits after). **Every tool takes `repo`**, defaulting to `CODE_CONTEXT_DEFAULT_REPO`; the filter lives in one helper so a new tool cannot quietly go unscoped. |
| `db/` | The derived index: `connect()` + `migrate()`. Schema evolves via raw-SQL **migrations** (`migrations/NNNN_*.sql`, yoyo over psycopg3 — see `migrations/README.md`): `code.fragment` = pgvector unit, `code.edge` = relation graph. |
| `embeddings.py` | The only caller of the embed model (Ollama, `nomic-embed-text` → 768) + the pgvector literal helper. Swapping models is a config change. |
| `llm.py` | The only caller of the **analyzer** model (the generative counterpart of `embeddings.py`). Three engines behind one `generate()`: local Ollama, the **cloud tier** for a model prefixed `anthropic:` (C-4 escalation, official SDK, lazily imported from the `[cloud]` extra), and the **OpenAI-dialect tier** for `openai:` — a company gateway over plain httpx, key from env only. The model string alone selects the engine — it already feeds the incremental keys, so a separate provider knob could drift under them. Strips the qwen3 `<think>` preamble locally and the `/no_think` directive on the way out. Swapping/escalating models is a config change. |
| `lifecycle.py` | The C-6a handshake with ai-life over its `/v1/model-profile`: signal `coder-active` and wait for the confirmed downshift **before** the local analyzer model loads, unload + confirm + signal `normal` on release, idle TTL in between. Opt-in (`CODE_CONTEXT_LIFECYCLE_*`, default OFF) — with the flag off it makes no calls and this repo is standalone. Called from `llm.py` (acquire) and `dev.py` (release). |
| `indexer/` | Builds the index from a repo. `java.py` = tree-sitter chunker (types + methods, exact names/lines) + `parse_edges` (imports/calls). `__init__.index_repo` walks `*.java` → embed → upsert `code.fragment` (incremental by hash) + rebuild `code.edge`. `notes.py` + `enrich_repo` = the C-4a leaf pass (a `note` per non-trivial class, anchored to real signatures). `rollup.py` + `rollup_repo` = the C-4b bottom-up pass: directory→module→project notes synthesized over the leaves (root → `project`, marker dir → `module`), incremental on each dir's input digest. **Pass-through directories are collapsed** (`collapse_chains`): a dir with no classes of its own and exactly one child costs an LLM call to restate its child, so the parent links straight through — except the root and module-marker dirs, whose tiers are meaningful. Deep Java packages make these chains the common case. `docs.py` + `ingest_docs` = the C-3 docs pass (exported HTML, `.docx` and `.pdf` → sections → `doc` fragments, no LLM; a binary format is converted to markdown and archived as Layer 1 first — D-6/D-7), and `link_docs` writes the doc→class `mentions` edges over the same `code.edge` table. |
| `agents_md.py` | The authored layer's starter (C-7): renders `AGENTS.md` **into the target repo** — a retrieval protocol naming the six tools, plus a map of top-level areas read back from the index (`module_map`). Rendering is pure (the map is an argument), so it is testable without a DB, and an unreachable index degrades to an honest "not indexed yet" rather than blocking the file. Never overwrites an authored file without `--force`; states no convention of its own. |
| `dev.py` | Dev CLI: `db-ping`/`migrate`/`embed-smoke` (infra) + `index`/`enrich`/`rollup`/`ingest`/`link`/`search`/`usages`/`deps` (drive the indexer) + `agents-md` (the authored starter). |

Dev infra lives in `infra/docker-compose.yml` — an isolated `pgvector/pgvector:pg16` container on host
port 5433 (ai-life's DB owns 5432), so an experimental index never touches production data. A
`db-backup` sidecar (OSS `postgres-backup-local`) takes a daily `pg_dump` into `infra/backups/`
(7 daily + 4 weekly), symmetric with ai-life's backup — though this index is derived/rebuildable and
prod rides ai-life's already-backed-up Postgres, so it matters mainly for a standalone deployment.

### Storage (decided, C-0)

- **Postgres, schema `code.*`, on the shared ai-life instance** (locked decision, roadmap).
- **md notes are the source of truth (Layer 1); the DB is a derived, rebuildable index.**
- **Graph = a plain `code.edge` table. Apache AGE is NOT warranted (decided on data).** The indexer
  prototype indexed ai-life `memory-service` (1168 edges) and `find_usages` / `get_deps` are both single
  **1-hop joins** on `code.edge` — no multi-hop traversal. The real precision gap is *type resolution*
  (calls matched by simple name), which is the Java sidecar's job (C-8), not something AGE solves. So AGE
  stays deferred (mirroring ai-life's `memory.relations`); revisit only if a future tool needs
  variable-length / transitive cypher (e.g. a full transitive-dependency closure or call-chain). If that
  day comes, the custom Postgres-with-AGE image becomes a base image shared with ai-life.

**The stored vocabularies live here, not in the DDL.** `kind` and `source` are free text, and a
committed migration must never be edited — including its comments — so the initial schema points
back at this list rather than carrying one that silently goes stale:

| Column | Values | Meaning |
|---|---|---|
| `fragment.kind` | `class` · `method` | parser facts (C-2) |
| | `note` | an LLM leaf note about a class (C-4a) |
| | `directory` · `module` · `project` | the rollup tiers, bottom-up (C-4b) |
| | `doc` | one section of an ingested document (C-3) |
| `fragment.source` | `facts` · `llm` · `docs` | how it was produced — and `docs` is the untrusted one, which is why `search_code` excludes it |
| `edge.kind` | `calls` · `imports` · `contains` | parser-derived; `index_repo` owns and rebuilds exactly these |
| | `mentions` | a doc section names an indexed class (C-3 / D-3); written by `link_docs` |

Adding a value is a code change, not a migration — that is the point of keeping them free text.

### Model use during indexing (model-agnostic, local-first)

Leaf/class notes — `notes_model` (a local coder); the dir/module/project rollup — `rollup_model`, which
the roadmap escalates to a strong model (Opus) for cross-file reasoning. Both tiers still **default to
local** — escalation costs money per directory and needs a key — and the pipeline stays
**model-agnostic**: model strength affects note *richness*, not whether it runs. Dev (pre-Mac) runs
against the Windows Ollama (`nomic-embed-text` + `qwen3:8b`); both golden lanes prove the passes there.

**Three engines, selected by the model string.** A bare tag is an Ollama tag; `anthropic:…` is the
Messages API; `openai:…` is an **OpenAI-dialect endpoint** at `CODE_CONTEXT_OPENAI_BASE_URL` — in
practice a company gateway fronting several models on one URL and one key (the dialect ai-life calls
`openai-compatible`). That tier is hand-rolled over httpx rather than pulling the OpenAI SDK: it is a
single non-streaming POST, and a work machine should not need an extra installed to reach its own
gateway. **Its key is read from the environment (`CODE_CONTEXT_OPENAI_API_KEY`) and is deliberately
not a `Settings` field** — the settings object is the sort of thing that gets printed while
debugging, and a secret in it is one `print(settings)` away from a log. Only the *local* tier signals
the C-6a lifecycle handshake, because only it loads a model onto the shared machine.

**Escalating a tier is a config change, not a code change.** Prefix any analyzer model with
`anthropic:` (e.g. `CODE_CONTEXT_ROLLUP_MODEL=anthropic:claude-opus-4-8`) and `llm.generate` routes
that call to the Messages API instead of Ollama; install `uv sync --extra cloud` and let the SDK find
the key (`ANTHROPIC_API_KEY` or an `ant auth login` profile — **code-context never reads, stores or
logs a key**). The prefix rides on the model string on purpose: the model already feeds
`notes.facts_key` / `rollup.inputs_digest`, so escalating a tier re-generates its notes instead of
silently keeping the local ones — the same defect the first real-repo run surfaced for model swaps.
The cloud call uses adaptive thinking (cross-file synthesis is the reason to escalate at all) with
`cloud_max_tokens` as the ceiling; `num_ctx` is an Ollama window and is not sent, and the
`llm.context_pressure` warn is local-only — the cloud window dwarfs any rollup prompt.

**The two tiers get separate call budgets.** A leaf note sees one class, so `notes_num_ctx` /
`notes_timeout_s` are small by design; a rollup aggregates every child note of a directory, and the
module/project tiers are the largest prompts the pipeline ever sends — hence `rollup_num_ctx` /
`rollup_timeout_s` (`llm.generate` takes both per call). Sharing the leaf budget silently truncated
big rollup prompts and timed the call out at exactly the most valuable tier.

**The analyzer model is part of the incremental key** (`notes.facts_key`, `rollup.inputs_digest`),
because it is part of the output: swapping models must re-generate, not skip. Keying on code facts
alone made a model swap a silent no-op.

### Docs ingest (C-3) — built (D-1…D-7)

D-1 the HTML → section parser · D-2 the ingest pass (`doc` fragments, `source='docs'`, incremental
by content hash) · D-3 the doc↔code `mentions` edges · D-4 the two tools over the corpus, plus the
repo scoping described under §Retrieval scope · **D-6 `.docx`** · **D-7 `.pdf`**. A golden lane drives the whole path
against a live engine (`tests/test_golden_docs.py`) — cheap, because no analyzer model is involved.

**Binary formats convert to markdown first (D-6).** A `.docx` goes through mammoth (Word styles →
semantic HTML) and markdownify, and the markdown — not the binary — is what gets chunked. The
converted file is **archived as the Layer-1 record** (`<docs>/.code-context/md/<rel>.md`, or
`CODE_CONTEXT_DOCS_MD_ROOT`): the source is neither greppable nor diffable, so without it the only
readable form of the document would be rows in the index — the same md-as-source rule the notes pass
follows. Word has no structure beyond styles, so a document that fakes its headings with bold 16pt
text degrades to one untitled section; that is honest, where inferring headings from font size is a
guess. `parse_markdown` and `parse_html` drive **one** section builder, so containment cannot drift
between formats.

**A `.pdf` has no structure at all (D-7)** — only glyphs at coordinates — so its tree is *inferred*:
the body size is the **smallest font size carrying a substantial share of the text** (weighed by
characters), and a line set larger than it is a heading whose level follows that size's rank. Two
weaker rules were tried and rejected: most lines, and most characters — each lets a heading win on
a short document, which promotes the heading size to "body" and suppresses every heading in the
file. Tables are lifted out first and their glyphs excluded from the text pass, so a rule living in
a table is rendered once, not twice. Uniform type yields one untitled section: the honest limit of
the format. **A scan (images, no text layer) yields nothing and is reported, not ingested** — OCR is
deliberately not wired up, and "cannot read this yet" must stay distinguishable from "read it, it
was empty".

**Two tools, deliberately different questions.** `search_docs` is the flat corpus search — the
mirror of `search_code`, which excludes docs. `find_convention` is task-anchored: given the class
you are writing, the sections that *name* it (the D-3 `mentions` join — an observed reference, not
a similarity guess) come first, and semantic hits fill the remaining budget so a rule that never
spells the class name out is still reachable. Both return `Doc` rows, not `Fragment`s, so ingested
prose can never be mistaken for an indexed code fragment: `source='docs'` and a `trust` note travel
with every row. When the authored layer lands (`AGENTS.md` / OpenSpec, C-7) it joins `find_convention`
as a third, *trusted* source ranked ahead of both — the shape already carries what distinguishes them.

### Retrieval scope

One index holds several projects (and their docs corpora), so **every tool takes `repo`**, falling
back to `CODE_CONTEXT_DEFAULT_REPO`; only an empty default searches everything, which is right only
for a deliberate cross-repo index. This was a live defect, not a hypothetical: with a docs corpus
and a second, unrelated project in one index, results came back interleaved. The filter is built
by a single helper (`tools._repo_clause`) precisely because six tools each remembering to add a
`WHERE` is how it went wrong the first time.

The code half is built; the docs half is empty (`find_convention` / `search_docs` raise
`NotImplementedError`). This is the larger gap: notes describe *what the code does* but not *why the
domain works that way* — on a real repo the analyzer wrote "life situation-based creation" straight
from a signature, with no idea what a life situation means in the business. That knowledge lives in
Confluence, not in signatures.

**Source: a Confluence HTML export, dropped in a directory** (roadmap decision B: manual export
first, REST sync later). One parser, the most common source, and the structure that matters —
headings, tables, code blocks — survives the export. `.docx`/`.pdf` are deliberately out of the
first slice; the parser seam is shaped so they slot in as another front, not a rewrite.

**Shape: a section is the unit.** A document is parsed into a tree by heading level; each leaf
section becomes one fragment carrying its **heading path** (`Payments / Refunds / Claim rules`) as its
symbol, so retrieval returns a slice that says where it came from. Tables are kept (in Confluence a
table *is* the rule, not decoration) and code blocks are kept verbatim. A document with no headings
degrades to one fragment; an oversized section is split with the heading path repeated on each part.

**Storage: reuse `code.fragment` — no migration.** `kind='doc'`, `path` = the document's relative
path, `symbol` = the heading path. `kind` is free text and the tiers already vary; a second table
would duplicate the embedding column, the HNSW index and every retrieval query for no gain.

**Linking doc↔code — the half that makes it more than a second search box** (D-3, built). After
ingest, doc text is scanned for the code symbols already in the index, emitting `code.edge` rows
(`kind='mentions'`, doc fragment → class fragment). That makes both directions answerable: *which
rules govern this class* and *which code implements this rule*. It reuses the existing 1-hop join,
so no new store — the same reasoning that kept AGE out.

Two properties are load-bearing:

- **Which names we link on is the only precision lever.** Matching is by simple name (the
  `find_usages` limitation; real resolution is the Java sidecar, C-8), so `indexer.docs.is_linkable`
  narrows the candidates instead: a multi-hump CamelCase name (`DecisionServiceImpl`) is a symbol
  wherever it appears, while a single-word or all-caps one (`Claim`, `SLA`) is the business
  wiki's ordinary vocabulary. Linking those would attach half the corpus to one class, and a wrong
  edge reads exactly like a right one — so the recall loss is taken deliberately, not hidden.
- **`index_repo` deletes only the parser's own edge kinds.** It replaces its whole edge set on every
  run; an unscoped delete would silently drop the `mentions` edges each time the code is re-indexed.
  `link_docs` is likewise a rebuild, not an append, so it is safe to re-run (`dev link <repo>`)
  whenever either side moves.

**Ingested text is untrusted — this is a boundary, not a caveat.** Anyone can edit a Confluence
page, so an ingested document can contain text aimed at the agent reading it. Therefore: doc
fragments are **data, never instructions**; retrieval hands them back tagged with provenance so the
caller can frame them as reference material; scripts and embedded markup are stripped at parse time;
and the fragments are marked (`source='docs'`) so a downstream consumer can always tell code-derived
content from ingested content. C-9 hardens this further; the boundary is designed in from the start
because retrofitting a trust boundary does not work.

**No LLM in the core pass** — parse, chunk, embed, link. That is what makes this the one phase that
runs comfortably on a CPU-only box. Distilling pages into thin `AGENTS.md` conventions *is*
model-work (roadmap 0.3) and is a separate, later step on the Mac.

**Observability** follows §Observability: `docs.parse` (document name, section count),
`docs.convert` (D-6/D-7: source `format`, markdown chars, and a **warn** when a document yields no
text — a scanned PDF — or its markdown cannot be written), `docs.ingest` / `docs.prune` (run counts), `docs.link` (per document: symbols matched) and
`docs.link_pass` (documents, linkable classes, edges) — names and metrics only. Doc
text is customer content and falls under the same never-log-payloads rule as source code.

**Fixture** (per the `new-golden` skill — a fixture that can actually fail): synthetic
Confluence-style HTML containing the shapes that break parsers, not the ones that flatter them —
a heading level skipped (h1→h3), a page with no headings at all, a rule that exists only inside a
table, a section too large for one fragment, a page mentioning real class names (to exercise
linking), and a page containing injection-shaped text (to prove it is carried as data).

### Observability (logging)

Indexing runs are long, unattended and remote-bound (the Mac), so the pipeline has to be
*explainable after the fact* without re-running it. That is the whole job of this layer.

**Contract: JSON Lines on stderr, one self-contained event per line.**

- **stderr, always.** The MCP server owns **stdout** — it is the protocol channel, and one stray
  line there corrupts the session. Logging to stderr is a correctness constraint, not a preference.
  The dev CLI keeps its human summaries on stdout, so `dev enrich` stays readable while its event
  stream is piped or redirected independently.
- **JSON Lines** (`CODE_CONTEXT_LOG_FORMAT=json`, the default) so a collector can ship the stream to
  Elasticsearch/Kibana with no grok pattern. `text` renders the same events human-readably for local
  eyeballing; it is a rendering choice only — the events are identical.
- **The app never talks to a log stack.** No ES client, no network calls in the indexer: it writes a
  stream, and a collector (Filebeat/Promtail) ships it. The pipeline stays runnable offline and
  independent of whether a log stack exists at all.

**Never log payloads — at any level.** Prompts, class bodies and note text carry *customer source
code*, and these events are destined for a central, searchable, retained index. Events therefore
carry **names and metrics only**: the input name (`DubService.java`, or the directory being rolled
up), the output name (the `.md` written), model, sizes, durations, outcome. The content itself
already exists on disk under `.code-context/notes/` for anyone who needs it. This is a hard rule, not
a level — there is deliberately no switch that turns payload logging on.

**Fields.** `@timestamp` (ISO-8601 UTC) · `log.level` · `event.action` · `event.duration` (ms) ·
`run_id` · `repo` · plus per-event fields. Names are ECS-aligned where that is free, and **stable**:
renaming one breaks saved Kibana queries. `run_id` is minted per process so a whole `enrich`/`rollup`
invocation groups as one trace, and `repo` is present because several repos land in one index.
Exceptions are collapsed onto a single line — a multi-line traceback would be split by the shipper
into unrelated documents.

**Event vocabulary.**

| `event.action` | Emitted by | Carries |
|---|---|---|
| `llm.generate` | `llm.py` | `provider` (`ollama` / `anthropic`), model, `prompt_chars`, `response_chars`, duration, outcome |
| `llm.context_pressure` | `llm.py` | model, `prompt_chars`, `num_ctx`, estimated fill — **warn**, local tier only |
| `embed.batch` | `embeddings.py` | model, `count`, duration |
| `lifecycle.signal` | `lifecycle.py` | `profile` (`coder-active` / `normal`), gateway url, duration, outcome |
| `lifecycle.unload` | `lifecycle.py` | model, duration (includes the `/api/ps` wait), outcome |
| `lifecycle.release` | `lifecycle.py` | `reason` (`stop` / `idle` / `exit`), models, `restored` — **warn** when ai-life could not be restored |
| `enrich.note` | `indexer/` | input `.java` name, symbol, output `.md` name, duration |
| `enrich.skip` | `indexer/` | input name, reason (`trivial` / `unchanged`) |
| `rollup.note` | `indexer/` | directory, tier kind, `children`, output name, duration |
| `index.file` / `index.run` | `indexer/` | path or counts, duration |
| `tool.<name>` | `server.py` | tool name, `result_count`, duration — **not** the query text |
| `run.start` / `run.finish` | `dev.py` | command, repo, counts, duration, outcome |

`llm.context_pressure` exists because silent truncation raises nothing by construction: the engine
just drops the overflow. Comparing prompt size against the configured `num_ctx` is the only way it
becomes visible, and it is how a too-small rollup window would now announce itself.

**Levels** (`CODE_CONTEXT_LOG_LEVEL`, default `INFO`) change *how much* is emitted, never *whether
payloads are* — that answer is always no. `INFO` = run start/finish, counts, warnings; `DEBUG` adds
the per-node events (`enrich.note`, `rollup.note`, `llm.generate`) with their timings.

## Performance & quality (measured + expected)

**Speed.**
- *Retrieval (per LLM turn)* — sub-second: one query embed + a pgvector HNSW cosine scan; `find_usages`/
  `get_deps` are millisecond 1-hop SQL joins. This is the cost that recurs per interaction, and it's cheap.
- *Indexing (one-time, then incremental)* — measured **202 fragments + 1168 edges in ~43 s** for one
  module on a Windows CPU with local nomic. It's embedding-bound: the full monorepo is a one-time cost
  (tens of minutes → a couple hours), then incremental (content-hash skip re-embeds only changed
  fragments). Metal on the Mac will be faster than this dev run.

**Quality — two independent things.**
- *What we control: context quality.* RAG is the mechanism that lets the model reason well — each call
  sees a small, precise, **connected** slice (~8–20k) instead of the whole repo, which is what avoids
  "lost in the middle" and hallucination. Retrieval precision is proven (golden lane). The **LLM-notes**
  lever has landed: **leaf notes (C-4a)** annotate each non-trivial class with intent on top of the parser
  facts, and **rollups (C-4b)** synthesize those bottom-up into directory→module→project notes — so an
  agent can pull a whole-subsystem summary or a single class' intent, not just syntax ("pay once at index
  time for cheap-and-good retrieval later"). Then connected-minimum retrieval (a method + its deps + its
  usages) and task **skills** (proven recipes) lift reliability further.
- *What we don't control: model tier (REFERENCE §2).* A local 32B handles ~80% of coding well (generate
  tests, method-level refactor, boilerplate, explain code, routine scans) — for these, good RAG → a ready
  solution. The hard ~20% (whole-project architectural reasoning, subtle bugs in unfamiliar code, long
  multi-file refactors) exceeds *any* local model; there we **escalate tiers** (cheap open API → Opus/GPT).
  The MCP is model-agnostic, so escalation is a config switch, not a rewrite.

**Honest gap:** end-to-end **generation** quality is only *structurally* checked so far. The C-4a/C-4b
golden lanes drive real models and assert the pipeline (right classes noted, carriers skipped, the right
rollup tiers built + retrievable) — but not note *richness*, and no coding-task solution is graded yet.
"Does the LLM give a ready, correct solution" gets fully validated with the shell (C-6) — that's where a scored LLM-golden
lane (like ai-life's `@GoldenLlmTest`) belongs.

## Build order

Done: foundation (**C-0**) → index + retrieval (**C-1/C-2**) → docs ingest + doc↔code links
(**C-3**) → enrichment, leaf + rollups (**C-4**, minus Opus escalation). Remaining: onboarding kit
(C-5) → shell + SDD loop (C-6/C-7, plus the C-6a lifecycle signal) → per-stack plugins + security
(C-8/C-9). Phase-by-phase state with what is still open inside each:
[`plans/roadmap.md`](plans/roadmap.md).
