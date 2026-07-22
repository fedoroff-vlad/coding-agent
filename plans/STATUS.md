# STATUS — coding-agent (update at the end of each PR)

**Where we are (2026-07-21):** phases **C-0…C-4 shipped**, **C-3 closed** — the index, the graph, the
LLM notes and the docs corpus all run end to end, and no MCP tool is a stub. **C-6a (the lifecycle
signal to ai-life) is built**, ahead of C-6. **Not started:** the onboarding kit (C-5), the agent
shell (C-6), the SDD layer (C-7), the Java sidecar (C-8), security hardening (C-9). **Open inside shipped phases:** the docs front reads only a
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

- **Code blocks really are verbatim now (C-3 defect fix) — DONE.** `_clean` normalises whitespace so
  an exported page's markup does not leak into the text; run over a whole section it also flattened
  the **indentation of code blocks**, which D-1 promises to keep verbatim — a retrieved Java or YAML
  example is the convention's example precisely because of its shape. `_clean_outside_fences` now
  splits the section on fenced spans and cleans only the prose between them, reusing
  `parse_markdown`'s fence scanner (closing run ≥ opening, so a fence can quote a fence) rather than
  a second rule that could drift from it. The fence is the right boundary because **both** parsers
  already mark code with one — `parse_html` wraps a `<pre>`, markdown arrives fenced. An
  unterminated fence keeps its tail verbatim: a truncated example is still an example. **The fixture
  was the reason this shipped** — its `<pre>` held two flush-left statements, so a flattening pass
  was invisible; it now carries an indented, blank-line-containing block, and the three new tests
  were verified to fail without the fix. Not fixed here and still true: `_split` cuts an oversized
  section on `\n\n`, so a very large fenced block can still be split mid-fence.

- **Lifecycle signal to ai-life (C-6a) — DONE.** `lifecycle.py`: the coder-side half of ai-life's
  LC-4. `acquire(model)` sits immediately before the local analyzer POST in `llm.py` — that POST
  *is* the load, so it is the last point at which the ordering can still be honoured — signals
  `coder-active` to ai-life's `/v1/model-profile` and **waits for the confirmed downshift**;
  `release(reason)` unloads our model (`keep_alive: 0`), polls `/api/ps` until it is really gone,
  and only then signals `normal`. Wired to the end of a dev-CLI command, an idle TTL, and an
  `atexit` backstop. **Opt-in, default OFF** (`CODE_CONTEXT_LIFECYCLE_*`) — with the flag off not
  one HTTP call is made and either project runs standalone, which is the whole point of the
  add-on framing on both sides.
  **Built before C-6 deliberately:** the slice was filed under "the shell's session lifecycle", but
  the thing that actually loads a 30B model on the shared Mac today is the *analyzer* pass, so the
  ceiling is at risk with or without a shell — and `llm.py` is a far more honest home for the
  handshake than a shell adapter would be (one choke point, cloud calls excluded for free, and the
  gate cannot be bypassed by a future caller).
  **The asymmetry is the design:** failing to get a *confirmed downshift* raises and loads nothing
  (carrying on is the over-budget load the mechanism exists to prevent); failing to *restore*
  ai-life afterwards leaves the box under budget, so it is logged (`restored=false`), not raised.
  Same reason `keep_alive: 0` is not treated as proof — it returns before the memory is freed.
  Embeddings are **not** gated (nomic is a few hundred MB and gating it would make every `index`
  run wait on ai-life) and the `anthropic:` tier loads nothing locally, so it never signals.
  11 unit tests own the **ordering** rather than the plumbing (LC-4 makes it a correctness
  requirement, so the guarantee belongs in the suite): signal-before-load, refusal/unreachable →
  nothing loaded, unload→confirm→restore, an unconfirmed eviction never signalling `normal`.
  Verified they can fail by moving `acquire` after the POST (3 red). **Not yet driven against a
  live gateway** — ai-life's endpoint does not exist yet (LC-4), and by design an absent endpoint
  reads as a refusal, so the flag stays off until it ships.
  *Bycatch:* `tests/test_obs.py`'s capture fixture set `logger.level` directly, which skips
  `setLevel`'s cache invalidation — so the first test module to emit an event before it silently
  blanked its captures. Fixed in the fixture; it was latent, not new.

- **OpenAI-dialect analyzer tier + the work-machine runbook — DONE.** A third engine behind
  `llm.generate`: prefix a model `openai:` and the call goes to `CODE_CONTEXT_OPENAI_BASE_URL` as a
  `/chat/completions` — i.e. **a company LLM gateway**, one URL and one key fronting several models.
  Hand-rolled over httpx instead of the OpenAI SDK (one non-streaming POST; a work machine should
  not need an extra installed to reach its own gateway), so the `[cloud]` extra stays
  Anthropic-only. **The key is env-only and deliberately not a `Settings` field** — a secret in the
  settings singleton is one `print(settings)` from a log; a test asserts no settings field name
  contains "key". `openai_suppress_thinking` sends `reasoning_effort: none` (off by default, since
  a gateway that rejects unknown fields would fail every call) — ai-life's hard-won lesson that the
  qwen `/no_think` tag does nothing over an OpenAI `/v1` endpoint. Drift-lint's `is_cloud_model`
  learned the prefix, or checks 4/5 would read it as an un-pulled Ollama tag. 5 new routing tests,
  verified red without the branch.
  **The runbook is the other half of the slice** (README §Use it on a work machine), and it exists
  because the useful discovery is what you *don't* need: retrieval — `index`, all four code tools,
  the docs ingest — touches **no analyzer at all**, only embeddings, so a work machine is productive
  before any gateway is configured. Embeddings deliberately stay on local `nomic-embed-text`:
  moving them remote changes `embed_dim` *and* `vector(N)`, i.e. a migration plus a full re-index.
  Step 0 of the runbook is `scrub-identity` + `.private-terms`, because indexing an employer's repo
  writes notes into it and this public repo has already been recreated once over exactly that.
  **Driven live** — not against the corporate gateway (it is behind that network), but against a
  real OpenAI-dialect server: **Ollama's own `/v1`**, which is the same dialect and cost nothing to
  point at. Body shape, response parsing and `<think>`-stripping all hold, and the suppression flag
  measured **33.5 s → 3.7 s** on `qwen3:8b` for the same one-word answer — which also came back
  clean instead of trailing a stray `/think`. That local `/v1` is the standing smoke for this tier.

- **Work-machine quickstart + opencode wiring — DONE.** `scripts/work-win.ps1`: one command from a
  clean work box to a shell with hands in the codebase — dev session (delegated to `start-win.ps1`
  rather than duplicated) → **only** `nomic-embed-text` (not `pull-models.ps1`: a machine driving a
  company gateway needs no analyzer model, and the difference is ~274 MB vs tens of GB) → index the
  target repo → register the `code-context` MCP server in opencode → install the skills.
  `-WireOnly` skips the infrastructure half, which is both the "refresh my skills" mode and what
  makes the risky half testable without Docker.
  **Two things the research changed.** opencode discovers `SKILL.md` in **six fixed locations only**
  — no configurable path, no documented symlink support — so a submodule under `tools/` is invisible
  to it and the skills must be *installed* (copied to `~/.config/opencode/skills/`, global rather
  than into the work repo, where they would show up in someone else's diff). And the MCP config
  lands in a file that **already holds the provider config for the work model**, so the script
  merges into it (with a backup) instead of writing it, and refuses to touch an `opencode.jsonc`
  at all — machine-rewriting JSONC deletes the comments — printing the entry to paste instead.
  **Verified here** against a redirected `XDG_CONFIG_HOME`: fresh-create, merge (provider, model
  and a foreign MCP entry all survive), re-run idempotency, the JSONC branch leaving the file
  untouched, no UTF-8 BOM (PowerShell 5.1's default writer emits one and it trips strict JSON
  parsers), and 11 skills installed with their `SKILL.md`. Caught two of my own bugs on the way:
  `-notmatch` on a string array filters rather than returning a boolean (so the "model already
  pulled" check was always true), and `\"` does not escape a quote in PowerShell.
  **Not verified end-to-end**: the infrastructure half — Docker on this VDI wedged mid-session
  (`docker ps` itself stopped responding after the full Testcontainers `verify`), so steps 1–3 have
  only ever run as `start-win.ps1`, which is unchanged and was already in use.

- **The shell actually reaches a model (C-6, work profile) — DONE.** The previous slice wired
  opencode's *hands* (the MCP server) and deliberately said nothing about its *head*, on the
  reasoning that retrieval needs no LLM — true, and it left the work profile one step short of
  usable: `work-win.ps1` wrote a config for a shell **that nothing installed** (opencode is not in
  `winget-packages.json`, and the script never checked), and it configured no provider, so the
  gateway the owner actually types into had to be set up by hand and was written down nowhere.
  Both closed here: `SST.opencode` joins the winget set, the script installs it when missing
  (re-reading PATH first — a shell that predates the install otherwise re-runs winget for a slow
  no-op), and `-GatewayUrl … -Model …` merges an `@ai-sdk/openai-compatible` **provider** block +
  the default `model` alongside the MCP entry.
  **One gateway, one key, two consumers** — opencode and `llm.py`'s `openai:` analyzer tier — so
  the key stays `CODE_CONTEXT_OPENAI_API_KEY` and the config gets opencode's `{env:…}` reference,
  never the value. **The key is deliberately not a parameter**: a secret on a PowerShell command
  line is in PSReadLine history and in the process list, which is the same reasoning that keeps it
  out of `Settings`. A missing key is reported at setup, where the cause is still on screen,
  rather than as a 401 at the first prompt.
  **Verified against a redirected `XDG_CONFIG_HOME`** — fresh-create; merge (a foreign provider,
  a foreign MCP entry and `theme` all survive, `model` switches to ours); re-run idempotency; the
  JSONC branch printing both snippets and leaving the file byte-identical; and the guards
  (`-GatewayUrl` without `-Model` throws, a base URL missing `/v1` warns, no `-GatewayUrl` at all
  ends with an explicit "the shell has nothing to think with"). Two things this cost: PowerShell's
  escape is a **backtick**, not `\` (the printed JSONC hint would have read `\"model\"`), and the
  script inherited `$LASTEXITCODE` from winget's non-zero "already installed" — so a successful
  setup exited non-zero. Both fixed; `Set-Prop` replaced the third copy of the
  add-member-or-assign dance.
  *Ops, not code:* Docker Desktop on the VDI had wedged again (`dockerd` alive, API not answering,
  `docker ps` hanging past two minutes, stale CLI processes from the previous session) — a restart
  of the engine brought it back and pgvector with it. That is the second occurrence; if it becomes
  routine the answer is an external-DSN path in the scripts, not a third manual restart.

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
5. **Then the unstarted phases in roadmap order:** C-6 is **partly done** — the shell is decided
   (**opencode**, decision D closed 2026-07-21) and the **work profile is complete**: installed,
   pointed at the company gateway, holding the MCP server and the skills (`scripts/work-win.ps1`);
   the C-6a signal it was to carry shipped earlier. What is left of it is the *Mac* profile — the
   same provider mechanism against local Ollama's `/v1` and `qwen3-coder:30b` — which needs the Mac.
   Still unproven on the work profile: a real prompt through the real gateway (the provider block
   is verified as *written*, not as *answering*), and `AGENTS.md` in the target repo, without which
   the shell has the retrieval tools but no instruction to prefer them over reading files. Then SDD wiring (C-7) → per-stack plugins +
   the Java sidecar (C-8) → security hardening (C-9).

## Known defects (found, not yet fixed)
- *(none — the `_clean`-flattens-code-blocks defect was fixed; see the slice above.)*

## Cross-repo pending (agreed, not yet done)
These are chores in *other* repos that this repo's work created. They live here because nothing else
tracks them, and a cross-repo tail is exactly what gets dropped at the end of a session.
- *(none open — the LC-4 tail closed the same day; see below.)*

**Closed 2026-07-21:**
- **ai-life: the `/v1/model-profile` endpoint — DONE** (ai-life#355, its slice LC-4). Both halves of
  the handshake now exist, so the pair may run together on the Mac **with both flags on**. It
  honours what our caller assumes: a **2xx means the outgoing model has actually left Ollama** (the
  endpoint polls `/api/ps` before answering), and every other status — including the 404 you get
  with `LLM_MODEL_PROFILE_ENABLED` off — stays a refusal. Verified live there (`qwen3:8b` ⇄
  `qwen2.5:7b`, a real swap in ~10 s), which is the wait a coder session pays at start and at stop.
  ai-life's stale "the caller is orphaned" note is retired in the same PR.
  **Still ours and not yet done:** driving *our* side against that live gateway — `lifecycle.py`
  has only ever talked to a stub. It is a two-flag, one-command check once both run on one box.

**Closed 2026-07-20:**
- **ai-life: bump the `agent-skills` submodule — DONE.** Verified rather than repeated: ai-life's
  pointer is already at `1bc78e9`, the same commit coding-agent pins and `agent-skills`' own
  `origin/main`, so all eleven skills (incl. `add-observability`, `new-golden`, `scrub-identity`)
  are visible there. The entry was stale, not outstanding.
- **ai-life: branch protection on `main` — DONE.** Enabled with coding-agent's exact shape: PR
  required, `build & test` required **and** strict (up to date with `main`), force-push and deletion
  off, `enforce_admins=true`. The check name is `build & test` — the job's `name:`, not its id, which
  is why this needed reading ai-life's workflow first. Recorded in ai-life's `CLAUDE.md` §Branching
  (ai-life#354), because a rule that is now enforced should not still read as honour-based.

## Infra
- **Daily DB backups — DONE.** `db-backup` sidecar (`infra/docker-compose.yml`, OSS
  `postgres-backup-local`): daily `pg_dump`+gzip → `infra/backups/`, 7 daily + 4 weekly; symmetric
  with ai-life's backup. This index is derived/rebuildable and prod rides ai-life's backed-up Postgres,
  so it's mainly for a standalone deploy. Off-site replication deferred.

## Open decisions (closed within slices; full list — [roadmap.md](roadmap.md))
Embeddings (leaning Ollama-local) · Confluence ingest (manual → API). **Shell choice closed
2026-07-21 — opencode** (roadmap §Open decisions D: chosen by adoption, and it satisfies the three
things we actually need — OpenAI dialect, local MCP servers, `SKILL.md`).

## Reminder
All repository `.md` are English; localization via optional `.ru.md` for user-facing content. One PR = one
slice. Spec before code. The model swap and hot/cold live on the ai-life side (`../ai-life/plans/lifecycle.md`).
