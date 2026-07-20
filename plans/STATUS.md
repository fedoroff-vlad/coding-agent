# STATUS — coding-agent (update at the end of each PR)

**Where we are (2026-07-20):** phases **C-0…C-4 shipped**, **C-3 closed** — the index, the graph, the
LLM notes and the docs corpus all run end to end, and no MCP tool is a stub. **Not started:** the
onboarding kit (C-5), the agent shell (C-6 + the C-6a lifecycle signal), the SDD layer (C-7), the Java
sidecar (C-8), security hardening (C-9). **Open inside shipped phases:** the docs front reads only a
Confluence HTML export (Opus rollup escalation closed 2026-07-20). Phase table with state:
[roadmap.md](roadmap.md). The bullets below are the slice-level record — what shipped and what each
slice cost us to learn.

## Now (shipped, oldest first)
- **C-0 (foundation) — DONE** (#1). Python scaffold, 6 MCP tools, `code.*` schema (AGE deferred),
  [`architecture.md`](../architecture.md). Tooling on **uv + Python 3.13 + uv.lock** (#2).
- **Dev infra — DONE.** Isolated pgvector container (:5433) + dev CLI + Ollama embeddings client.
- **Indexer prototype (C-2 facts + C-1 retrieval) — DONE.** tree-sitter Java chunker
  ([`indexer/java.py`](../src/code_context/indexer/java.py): types + methods, exact names/lines) →
  embed (nomic) → upsert `code.fragment` (incremental by content hash); `search_code` (pgvector cosine)
  + `get_file`. **Proven on ai-life** `platform/memory-service` (35 files → 202 fragments, 43 s); semantic
  queries return the right code (e.g. "wiki links → relations/backlinks" → `WikiLinkParser` /
  `NoteService.backlinks` / `RelationService`). Re-run incremental (0 re-embedded).
- **Graph edges (C-2 cont.) — DONE, and AGE decided.** imports/calls/contains → `code.edge`;
  `find_usages` + `get_deps`. On `memory-service`: 1168 edges. **Both are 1-hop joins → Apache AGE NOT
  warranted; plain `code.edge` stays.** Precision gap = type resolution (Java sidecar, C-8), not AGE.
- **DB migrations — DONE.** yoyo (raw SQL over psycopg3), `migrations/NNNN_*.sql`, one unified Python
  style (`migrations/README.md`); `db.migrate()`; `apply-schema`→`migrate` in dev CLI + start scripts.
- **Golden lane — DONE.** pytest `golden` marker (opt-in, excluded from unit CI via `addopts`) vs real
  Ollama + pgvector; a tiny fixture minirepo (auth/math/billing) + structure-not-text asserts (retrieval
  ranks the right class; `get_deps`/`find_usages` resolve). Run: `scripts/golden.{sh,ps1}`. 5/5 green live.
- **LLM leaf notes (C-4a) — DONE.** A semantic note per **non-trivial** class over the parser facts:
  triviality gate (records / accessor-only carriers → facts only, incl. record-style `amount()` getters
  by body shape), a prompt anchored to real class+method signatures (→ no invented symbols), the note
  written as **md** (`llm.py` analyzer = Ollama generate; md-as-source, Layer 1, in `.code-context/notes/`)
  and indexed as a `note` fragment (`source='llm'`). Incremental on `facts_key` (signatures, not body).
  `enrich_repo` + `dev enrich`; a second golden lane drives the real model (right classes noted, carriers
  skipped, note retrievable) — 4/4 green live on qwen3:8b. No schema change (reused `kind='note'`).
- **LLM note rollups (C-4b) — DONE.** Bottom-up dir→module→project notes synthesized over the leaf notes
  (`rollup.py` + `rollup_repo` + `dev rollup`): build the directory tree, roll each dir up from its
  components (child rollups + own leaf notes) deepest-first — root → `project`, a `pom.xml`/`build.gradle`
  marker dir → `module`, else `directory` (schema's own `kind`s → no migration). md-as-source
  (`notes_root/<dir>/_index.md`); incremental on each dir's input digest (a changed leaf re-flows up); prunes
  vanished dirs. `rollup_model` (local-capable default; Opus escalation needs a cloud provider in `llm.py`
  = follow-up). Golden lane 5/5 live on qwen3:8b (project/module/directory tiers, module detection,
  incremental rerun). drift-lint check 5 pins `rollup_model` ∈ pull list.
- **First real-repo run + the defects it surfaced — DONE** (#30, #31). Drove the full pipeline over
  a production Java service (143 files → 868 fragments, 7201 edges; 114 leaf notes, 59 rollups) on
  `qwen3:8b`. Three defects, none reachable from the golden fixture: the Windows launcher installed
  neither `uv` nor started Ollama; `rollup` ran on the *leaf* call budget (ReadTimeout at the
  module/project tier + silent 8k truncation) → its own `rollup_num_ctx`/`rollup_timeout_s`; and the
  analyzer model was absent from the incremental keys, making a model swap a silent no-op → it now
  feeds `notes.facts_key` / `rollup.inputs_digest`. **Quality read:** leaf notes are anchored and
  specific (they name real methods); the *root* rollup is diluted — the seven-level single-child
  package chain restates itself upward — fixed by the collapse slice below.
- **Observability / logging — DONE.** `obs.py`: JSON Lines on stderr (stdout is the MCP protocol
  channel), `run_id` per process, `event.action` vocabulary + `event.duration`, ECS-aligned stable
  field names, exceptions collapsed to one line, `CODE_CONTEXT_LOG_LEVEL` / `_LOG_FORMAT`. Wired into
  `llm.generate` (+ `llm.context_pressure`, the warn that makes silent truncation visible),
  `embeddings.embed`, the `enrich`/`rollup` per-node passes, all 6 MCP tools, and the dev CLI's `run`
  event. **Names and metrics only — payloads are never logged at any level** (customer source code;
  events are built to leave the machine for Elasticsearch/Kibana, shipped by a collector — the app
  holds no ES client). Contract in `architecture.md` §Observability; 7 tests including an explicit
  no-payload-leak assertion.
- **Rollup chain collapse — DONE.** `rollup.collapse_chains`: a directory with no classes of its own
  and exactly one child is a pass-through — it spends an LLM call restating its child on an
  ever-growing prompt — so its parent now links straight through. The root (the `project` tier) and
  module-marker dirs are never collapsed; survivors keep their full path so retrieval still resolves
  them. Deep Java packages make this the common case: on the production repo a seven-level
  single-child package chain was ~a fifth of rollup time and visibly diluted the root note.
  Stale `_index.md` files are now pruned from disk too (`_prune_rollup_md`) — md is the
  source-of-truth layer, so a dead rollup nothing marks as dead is worse than a missing one.
  5 unit tests (collapse, module/root preservation, dirs with own classes, branching no-op,
  input not mutated). **Golden lane not yet extended** — asserting the end-to-end shape needs a live
  engine; see Next.
- **Docs HTML parser (C-3 / D-1) — DONE** (#36). `indexer/docs.py`: an exported page → a section tree,
  the **section as the unit** carrying its heading path as `symbol`. Tables are rendered to text
  (in a wiki the table *is* the rule) and code blocks kept verbatim; `<script>`/`<style>` are dropped
  at the parse boundary, but the parser deliberately does **not** filter prose aimed at the agent —
  that boundary is provenance tagging, not censorship. Degrades sanely: a headingless page → one
  fragment, a skipped level (h1→h3) nests by containment, an oversized section splits with the path
  repeated. Pure (no DB/embeddings/model) + a synthetic fixture built to the `new-golden` rule.
- **Docs ingest pass (C-3 / D-2) — DONE** (#37). `ingest_docs` → `code.fragment` (`kind='doc'`,
  `source='docs'`), embed, incremental by section hash, stale sections pruned, `dev ingest` +
  `docs.*` events. **No LLM in the pass** (parse → chunk → embed), which is exactly why the docs
  phase suits the CPU-only dev box. Reuses `code.fragment` — a second table would duplicate the
  embedding column, the HNSW index and every query.
- **Doc↔code links (C-3 / D-3) — DONE** (#38). `link_docs(repo)` scans every `doc` fragment for the class
  symbols already indexed in the same repo and rebuilds the repo's `mentions` edges (doc fragment →
  class fragment) — so "which rules govern this class" and "which code implements this rule" are the
  same 1-hop join that kept AGE out. Matching is pure and unit-tested (`docs.find_mentions` /
  `docs.is_linkable`): whole-token, case-sensitive, and **only on multi-hump CamelCase names** —
  `Claim` / `SLA` are the business wiki's vocabulary, not its symbols, and a wrong edge reads
  exactly like a right one, so the recall loss is deliberate. Runs at the end of `ingest_docs` and
  standalone as `dev link <repo>` (idempotent rebuild). **Fixed in the same slice:** `index_repo`
  deleted *every* edge of the repo before re-inserting its own, so any code re-index silently wiped
  the docs links — the delete is now scoped to the parser's own kinds. 10 new unit tests.
- **The docs tools + repo-scoped retrieval (C-3 / D-4, D-5) — DONE** (#39). `search_docs` (flat corpus search)
  and `find_convention` (task-anchored: `mentions`-linked sections for the class you name first, then
  semantic hits) replace the last two `NotImplementedError`s — the MCP surface is now fully
  implemented. Both return a **`Doc`** row, not a `Fragment`: `source='docs'` + a `trust` note travel
  with every result, so ingested wiki prose can never be mistaken for indexed code or read as an
  instruction. **The repo-scoping defect is closed with it:** every tool takes `repo`
  (default `CODE_CONTEXT_DEFAULT_REPO`) and the filter is built by one helper, because six tools each
  remembering a `WHERE` is exactly how it went wrong. A **docs golden lane** (D-5) drives the whole
  path live — meaning-not-keyword retrieval, provenance on every row, the D-3 link surfacing through
  `find_convention`, injection-shaped prose returned tagged rather than obeyed, and two repos in one
  index proving the scoping. 9/9 green live on nomic-embed-text; verified it *can* fail by breaking
  the scope helper (2 failed). No analyzer model is involved, so this lane is cheap on the CPU box.

- **Cloud analyzer tier (C-4 escalation) — DONE.** `llm.generate` now fronts two engines: local
  Ollama, and the Messages API for any model prefixed **`anthropic:`** (`[cloud]` extra, official
  SDK, imported lazily so a bare install and the local path never touch it). The roadmap's
  leaf-local / rollup-strong tiering is therefore a config change:
  `CODE_CONTEXT_ROLLUP_MODEL=anthropic:claude-opus-4-8`. **The provider rides on the model string
  rather than its own setting** — the model already feeds `notes.facts_key` / `rollup.inputs_digest`,
  so escalating a tier re-generates its notes; a separate `*_provider` knob could change underneath
  those keys and silently keep the local notes, which is exactly the incremental-key defect #31
  fixed for model swaps. Credentials are the SDK's (`ANTHROPIC_API_KEY` or an `ant auth login`
  profile) — **we never read, store or log a key**, and `prompt_chars` stays the only size signal.
  The cloud call uses adaptive thinking (cross-file synthesis is the point of escalating) capped by
  `cloud_max_tokens`; `num_ctx` and the `llm.context_pressure` warn stay local-only, and the
  qwen-specific `/no_think` tail is stripped rather than shipped as literal noise. `llm.generate`
  events now carry `provider`. Drift-lint learned `is_cloud_model` — checks 4/5 pin *local* defaults
  to the pull list, and a cloud model has nothing to pull. 7 unit tests over the routing (both
  engines stubbed: a real cloud call costs money and belongs in a lane, not unit CI); **not yet
  driven against the live API** — see Next.

- **`.docx` behind the same parser seam (C-3 / D-6) — DONE.** The docs pass now reads Word documents
  as well as exported HTML. A `.docx` is converted to **markdown first** — mammoth (Word styles →
  semantic HTML) then markdownify — and that markdown is **archived as the Layer-1 record**
  (`<docs>/.code-context/md/<rel>.md`, or `CODE_CONTEXT_DOCS_MD_ROOT`) before it is chunked: the
  binary is neither greppable nor diffable, so without the file the only readable form of the
  document would be rows in the index. Same md-as-source rule the notes pass follows. `parse_html`
  and the new `parse_markdown` drive **one** section builder (`_Builder`), so containment cannot
  drift between formats — and that builder plus `parse_markdown` are exactly the seam `.pdf` reuses.
  Word has no structure beyond styles, so a document that fakes its headings with bold 16pt text
  degrades to one untitled section: honest, where inferring a heading from font size is a guess.
  **Two real defects came out of the fixture**, which is the `new-golden` rule paying for itself:
  a bare `#` was emitted as a literal text fragment instead of being read as CommonMark's empty
  heading, and `zipfile.BadZipFile` (a `.doc` renamed to `.docx` — the common case) does not derive
  from `OSError`, so **one malformed document would have aborted a corpus-wide ingest**; the
  per-file guard is now deliberately broad and reports the error type on the event. Word's `~$`
  lock files and our own `.code-context` output are excluded from the glob. 20 unit tests, fixture
  built in-memory rather than committed as a binary blob (a `.docx` you cannot read in a diff
  cannot tell you why a parser test failed); verified it can fail by disabling fence handling and
  by stripping tables.

- **`.pdf` behind the same markdown seam (C-3 / D-7) — DONE.** Text-layer PDFs, reusing D-6's
  `parse_markdown` exactly as intended. **A PDF has no structure — only glyphs at coordinates** —
  so the section tree is inferred: body size is the *smallest font size carrying a substantial
  share of the text* (weighed by characters), and a larger line is a heading whose level follows
  that size's rank. Two weaker rules were written and rejected against the fixture: most lines, and
  most characters — each lets a heading win on a short document, promoting the heading size to
  "body" and suppressing every heading in the file. Tables are lifted out first and their glyphs
  excluded from the text pass, so a rule living in a table is rendered once, not twice. Uniform
  type yields one untitled section, which is the honest limit of the format rather than an invented
  tree. **A scan yields nothing and is reported** (`docs.convert` warn, no archived md, no
  fragment): OCR is deliberately out of scope, and "cannot read this yet" must stay distinguishable
  from "read it, it was empty". 11 unit tests over a PDF the fixture **writes by hand** — a
  generator that could not set per-line font sizes could not test the one heuristic that matters;
  verified it can fail in both directions (no heading ever detected / typographic jitter promoted
  to a heading). The fixture's own first draft had a real bug worth keeping in mind: leading sized
  from the *preceding* line lets a large line overlap a small one, and then the larger line's `top`
  is the smaller of the two — reading order silently reverses.

## Next
**Owner's call pending** between #1 and #2 below — both are ready to start, and #2 needs hardware we
do not have here.

1. **Semi-manual Step 0 (C-5)** on the owner's real Java monorepo — first `AGENTS.md` + hierarchical
   notes + baseline specs, i.e. the live spec for the onboarding kit. **Blocked on access**, not on
   code: needs the repo + Confluence export. The docs half of the pipeline it depends on is now built.
2. **Extend the golden lanes to the gaps the first real-repo run exposed.** The fixture repo is small
   and flat, which is exactly why three shipped defects (#30–#31) and the chain collapse were only
   visible on a 148-class repo. Needed: a **deep single-child package** (locks the collapse end-to-end
   and keeps a future refactor from resurrecting the per-level rollups), a **rerun under a different
   analyzer model** (proves the model-keyed incremental keys re-generate rather than skip), and a
   directory wide enough that a rollup prompt is genuinely large (exercises `rollup_num_ctx`). Drives a
   real analyzer, so it wants the Mac or a deliberate slow CPU run here. (The *docs* golden lane needs
   no model and already runs here.)
3. **Drive the cloud tier against the live API once.** The routing ships with stubbed engines, so
   what is *not* yet proven is the round trip: a real key, a real Opus rollup, and whether the
   escalated note is visibly better than the local one on a repo we already have baselines for
   (the production Java service). Cheap in wall-clock, not free in money — one deliberate run,
   ideally alongside #2 so the same pass checks both.
4. **C-3 residue, deliberately deferred and not forgotten:** **OCR for scanned PDFs** — D-7 reads
   the text layer and reports a scan rather than ingesting it blank; the owner's call was to keep
   OCR out until there is a real scan corpus, and to weigh a shared capability-MCP (as in ai-life)
   against an in-repo tesseract dependency when it comes; Confluence **REST sync** instead of a manual export (roadmap decision B); distilling
   pages into thin `AGENTS.md` conventions (roadmap 0.3 — that one *is* model work and belongs with
   C-7's authored layer). None of it blocks the next phase.
5. **Then the unstarted phases in roadmap order:** shell integration (C-6, still an open decision
   between Aider / Continue / Cline) + the lifecycle signal to ai-life (C-6a) → SDD wiring (C-7) →
   per-stack plugins + the Java sidecar (C-8) → security hardening (C-9).

## Known defects (found, not yet fixed)
- **`_clean` collapses whitespace inside code blocks.** D-1 promises code blocks survive *verbatim*,
  but every section's text goes through `_clean`, whose `[ 	]+ → " "` rule flattens indentation.
  A retrieved Java or YAML example therefore loses its structure. Pre-existing (D-1), inherited
  unchanged by D-6's markdown path, and cheap to fix — the fenced spans need to be held out of the
  whitespace pass — but it is a parser change with its own tests, not a rider on a format slice.

## Cross-repo pending (agreed, not yet done)
These are chores in *other* repos that this repo's work created. They live here because nothing else
tracks them, and a cross-repo tail is exactly what gets dropped at the end of a session.
- **ai-life: bump the `agent-skills` submodule.** `agent-skills` gained `add-observability` +
  `new-golden` (agent-skills#5); coding-agent bumped in #34, **ai-life still points at the old
  commit**, so those two skills are invisible there. One-line pointer change + PR.
- **ai-life: enable branch protection on `main`.** coding-agent enforces it server-side (PR required,
  required status check, force-push/deletion off, `enforce_admins=true`) after a direct push slipped
  through on 2026-07-19. ai-life has the same "never commit to main" rule and **no enforcement**.
  Needs the exact CI check name from ai-life's workflow before it can be set.

## Infra
- **Daily DB backups — DONE.** `db-backup` sidecar (`infra/docker-compose.yml`, OSS
  `postgres-backup-local`): daily `pg_dump`+gzip → `infra/backups/`, 7 daily + 4 weekly; symmetric
  with ai-life's backup. This index is derived/rebuildable and prod rides ai-life's backed-up Postgres,
  so it's mainly for a standalone deploy. Off-site replication deferred.

## Open decisions (closed within slices; full list — [roadmap.md](roadmap.md))
Embeddings (leaning Ollama-local) · Confluence ingest (manual → API) · shell choice (Aider/Continue/Cline,
evaluated in C-6).

## Reminder
All repository `.md` are English; localization via optional `.ru.md` for user-facing content. One PR = one
slice. Spec before code. The model swap and hot/cold live on the ai-life side (`../ai-life/plans/lifecycle.md`).
