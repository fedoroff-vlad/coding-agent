# coding-agent — instructions for Claude

Local coding agent: RAG-first, shares a 32B inference engine with ai-life on one Mac. We **build** a
RAG-MCP on top of a **reused** agent shell. Sibling project: ai-life (`../ai-life`).

## Session start — reading order
1. This file (`CLAUDE.md`).
2. `plans/STATUS.md` — what's in flight / the next slice.
3. `plans/INDEX.md` — map of the plans; pick the relevant one.
4. The one relevant plan (`plans/REFERENCE.md` — strategy + architecture; `plans/roadmap.md` — how we build it).

## Reusable dev-workflow skills  (submodule: tools/agent-skills/)
Portable skills shared with ai-life (source: github.com/fedoroff-vlad/agent-skills). Before doing one of these classes of work, read the matching `tools/agent-skills/skills/<name>/SKILL.md` and follow it instead of re-deriving:
- `new-skill` — author or fix a SKILL.md so it triggers reliably ("use when …" descriptions).
- `new-module` — scaffold a new module / MCP tool from the canonical layout.
- `check-drift` — after any change, verify every coupled artifact moved; the machine-checkable half of the "Change-propagation map" below (reads `.skills/change-map.yaml`).
- `close-pr` — move finished work out of `plans/STATUS.md`, freshness pass, squash-merge on green.
- `bump-deps` — bump a dependency across `pyproject.toml` + `uv.lock` + pins.
- `release-version` — cut a stable outgoing version (semver + changelog + tag).
- `run-goldens` — run the golden lanes (`pytest -m golden`: retrieval / notes / rollup) against a real Ollama via `scripts/golden.{sh,ps1}` — all, or a subset (pass a test path / `-k`); reads real regression vs flaky borderline case.
- `add-observability` — design logging for a new service/pass before wiring it: event vocabulary, sink, levels, and the never-log-payloads rule. Our `## Observability` contract is the reference implementation.
- `new-golden` — decide unit-vs-golden and author a fixture that can actually *fail* (our small flat fixture is why three defects reached a real repo first).
- `scrub-identity` — before recording WHOSE data a run was against (a client/employer repo name,
  package path, or that industry's vocabulary in a "synthetic" fixture) in docs, commits, tests
  or fixtures. Ships `check-private-terms.{sh,ps1}` — a local pre-commit check against a
  gitignored `.private-terms`. This repo learned it the expensive way: it is public, it named a
  third party's service, and because GitHub serves `refs/pull/<N>/head` forever, no force-push
  could remove it — the repository had to be recreated.
- `architecture-checkup` — audit the repo (or a change) against agent-engineering standards (manifests / SDD / TDD / drift / canon + security / runtime-hardware fit); emits a prioritized findings report. Find-only — hands fixes to `check-drift` / `new-module` / `new-skill`.

The coupling table they consume lives at `.skills/change-map.yaml`.

## Status
**C-0…C-4 shipped** (#1–#39). MCP scaffold + `code.*` schema; a tree-sitter Java indexer (fragments +
imports/calls/contains edges); pgvector `search_code`/`get_file`/`find_usages`/`get_deps`; yoyo migrations;
opt-in golden lanes — proven live on ai-life's `memory-service` and on a 143-file production Java service. Plus
**LLM notes**: `enrich` (C-4a, per-class leaf notes, trivial carriers skipped) and `rollup` (C-4b, bottom-up
dir→module→project notes, pass-through chains collapsed); md-as-source → retrievable fragments. Plus
**C-3 docs** (D-1…D-7): an exported corpus (HTML, `.docx`, text-layer `.pdf`) → sections → `doc`
fragments, doc↔code `mentions` edges, and `search_docs`/`find_convention` over them — so **no tool
is a stub any more**. A binary format is converted to markdown and archived as the Layer-1 record
before it is chunked; a PDF's sections come from a font-size heuristic, and a scan is reported
rather than ingested blank (no OCR by decision).
Ingested text is data, never instructions: every doc result carries `source`/`trust`. Retrieval is
**repo-scoped** (`CODE_CONTEXT_DEFAULT_REPO`) after two projects in one index came back interleaved.
Apache AGE stays decided against (every graph tool is a 1-hop join).

Plus the **cloud analyzer tier** (C-4 residue, now closed): prefix any analyzer model with
`anthropic:` and `llm.py` routes that call to the Messages API instead of Ollama, so the roadmap's
strong rollup tier is a config change; defaults stay local because escalation bills per directory.

Plus the **lifecycle signal to ai-life** (C-6a, `lifecycle.py`): signal `coder-active` and wait for
the confirmed downshift **before** the local analyzer model loads; unload + confirm + signal `normal`
on release (end of run / idle TTL / atexit). Opt-in, default OFF — with the flag off no call is made
and either contour runs standalone. An unconfirmed downshift *fails* the run, so it stays off until
ai-life ships `/v1/model-profile` (LC-4).

**Next:** a semi-manual Step 0 on the owner's real Java monorepo (C-5 — needs the repo + Confluence),
or first the golden-lane gaps the real-repo run exposed (needs a live model, so realistically the Mac).
**Still open inside shipped phases:** OCR for scanned PDFs + Confluence REST sync + `AGENTS.md`
distillation (C-3 residue). Live detail → `plans/STATUS.md` (the source of truth; keep in sync).

## Language convention (mirrors ai-life)
- **All repository `.md` files are English (canonical)** — plans, README, everything committed. This is the
  source of truth.
- **Localization via `<name>.ru.md` siblings** (optional): a Russian copy lives next to the canonical
  English file, exactly like ai-life's `SKILL.md` + optional `SKILL.ru.md`. Reserve it for **user-facing**
  content (skills / deliverables) that an end reader consumes; internal planning docs stay English-only
  (ai-life ships zero `.ru.md` — the mechanism is documented, used only where a reader needs it).
- **Chat with the owner stays Russian.** User-facing app replies follow the end user's language.

## Conventions
- **SDD basis (target — lands in C-7, not in the repo yet):** agents.md + OpenSpec + the kit's ready-made prompts (`github.com/sipki-tech/ai-coding-workflow-kit`). `AGENTS.md` / OpenSpec are the authored Layer-1 slice still marked `○ planned → C-7` in [`architecture.md`](architecture.md) §Status — don't go looking for them yet.
- **Reuse > rebuild:** reuse the agent shell — **opencode** (decision D, closed 2026-07-21); build only
  the differentiator — a RAG over code + Confluence. Anything the shell already does (edit loop, diffs,
  provider config) is not ours to build.
- **Tooling:** Python core + an optional Java sidecar. Models: local-first, tier 32B (leaf) / Opus (rollup).
- One PR = one small vertical slice. Spec before code.
- **Never commit to `main` — branch, PR, green CI, squash-merge. No exceptions.** This is enforced
  server-side (branch protection: PR required, `build-test (3.13)` must pass, force-push and deletion
  off, admins included), so a direct push is *rejected*, not merely discouraged. If you find yourself
  on `main` after a merge, branch **before** the first edit of the next slice — that is exactly where
  the slip happens.

## Change-propagation map — non-negotiable
A change is not done until **every coupled artifact** moves with it, in the **same PR**. Partial
propagation is silent drift. The mechanizable subset is enforced by
**[`scripts/check-consistency.sh`](scripts/check-consistency.sh)** (a CI step on every PR, incl.
docs-only — see `.github/workflows/ci.yml`); the rest is this human checklist. Touch the left → update
the right, and re-run the lint locally:

- **Ollama model / tag** → `scripts/pull-models.sh` **and** `scripts/pull-models.ps1` (keep the two in sync) · `src/code_context/config.py` (`embed_model`, `notes_model`, `rollup_model`) · `scripts/golden.{sh,ps1}` · `README.md` / `architecture.md`. The `config.py` default `embed_model`, `notes_model` **and** `rollup_model` must each be in the pull list (drift-lint checks 2 + 4 + 5).
- **Embedding model / dimension** → `config.embed_dim` **and** `vector(N)` in `src/code_context/migrations/0001_initial_schema.sql` (+ `CODE_CONTEXT_EMBED_DIM`). Swapping the embed model changes both.
- **New MCP tool / config env var** → `architecture.md` (tool contract) · `README.md` · the tool's stub/impl · `.env`-style docs.
- **New docs source format** → `_DOC_SUFFIXES` **and** the `_parse_document` dispatch in
  `indexer/__init__.py` (the glob and the dispatch are two reads of one list — a format in only one
  is silently skipped) · its parser in `indexer/docs.py` · the reader lib in the `[docs]` extra ·
  `architecture.md` §Docs ingest · `README.md` · `dev.py`'s `ingest` usage line.
- **New dependency** → `pyproject.toml` **and** `uv.lock` (`uv sync` / `uv lock`) — CI runs `--locked`.
  An **optional extra** additionally → `.github/workflows/ci.yml` (`uv sync` installs only the extras
  it names, so an unnamed extra's code is never imported in CI) · `README.md` §Layout.
- **Analyzer provider** (the `anthropic:` prefix routing in `llm.py`) → `scripts/check-consistency.sh`
  (`is_cloud_model`, or the pull-list checks read it as a missing Ollama tag) · `.env.example` ·
  `architecture.md` §Model use during indexing · `README.md`. Anything that treats a model string as
  an Ollama tag has to learn the prefix.

- **The ai-life handshake** (`lifecycle.py` — profile names, endpoint path, the ordering) →
  `tests/test_lifecycle.py` (the ordering *is* the contract) · `.env.example` · `architecture.md`
  §Contours + the event vocabulary · `README.md` · **and the other side of the wire**:
  `../ai-life/plans/lifecycle.md` §LC-4. This is the repo's only cross-repo contract, so a change
  here is silent drift by default — nothing in this repo's CI can see ai-life. Record it in
  `plans/STATUS.md` §Cross-repo pending in the same PR.

Extend the lint whenever a new coupling is mechanically checkable (a stale ref a grep can catch) — that's
how the "automat" grows instead of relying on memory. Mirrors ai-life's identical section.

## Relationship to ai-life
Shares the Mac (64 GB) with ai-life 24/7. The ai-life model swap (`qwen3:32b` ↔ `qwen3:14b` during a coding
session) lives in ai-life's `llm-gateway` (slice LC-4, `../ai-life/plans/lifecycle.md`). **Emitting the
signal is ours — slice C-6a, built** in [`lifecycle.py`](src/code_context/lifecycle.py) (opt-in, default
OFF; flag + gateway URL + idle TTL from env). Order is load-bearing: signal `coder-active` → wait for
ai-life's *confirmed* downshift → only then load the coder model; on stop/idle unload ours, confirm, then
signal `normal`. Either contour must run standalone. **Touching that ordering means touching
`tests/test_lifecycle.py`** — the ordering is the contract, and it lives in the suite, not in a comment.
ai-life's `/v1/model-profile` shipped 2026-07-21 (its LC-4), so **both halves now exist** — turn the flag
on only together with ai-life's `LLM_MODEL_PROFILE_ENABLED`, since it only steps down when asked.
