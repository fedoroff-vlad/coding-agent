# coding-agent — roadmap

Strategy and the "why" — in [REFERENCE.md](REFERENCE.md). Here — the **how**: the onboarding pipeline for
any project ("Step 0") + the continuous SDD work cycle + the phases of building the product itself.

> Language: **all repository `.md` are English** (canonical); localization via optional `<name>.ru.md`
> siblings for user-facing content. See [../CLAUDE.md](../CLAUDE.md).

## Locked decisions (2026-07-10)
- **Hardware:** one Mac Studio M4 Max 64/512, 24/7. Growth — by **adding a 2nd Mac** (split
  infra/inference), not by replacing. The swap + hot/cold are single-machine-phase concessions.
- **Models:** two + swap — ai-life `qwen3:32b` ⇄ `qwen3:14b` during a coding session, coder `qwen3-coder:30b`
  (Qwen3-Coder-Flash); **never two large models** resident; the swap lives in ai-life `llm-gateway` (LC-4), a
  clean unload is mandatory. Frontier open (Qwen3-Coder-480B, DeepSeek V4, GLM-5.2, Kimi K2) = cloud/escalation tier.
- **Context:** RAG-first (window ~8–20k), not a big window — this is what makes 64 GB real.
- **Tooling:** **Python core** + an optional **Java sidecar** (JDT/JavaParser) for deep semantics.
- **Scan:** parser facts + LLM notes bottom-up (class→directory→module→project); the pipeline is
  model-agnostic, **local-first**, tier 32B (leaf) / Opus (rollup); trivial classes — facts only.
- **Storage:** **md is the source (Layer 1), the DB (pgvector+AGE) is a derived index.** Shared ai-life
  Postgres, schema `code.*`.
- **Strategy:** **reuse** the agent shell (Aider/Continue/Cline) + **build** the RAG-MCP; SDD basis
  (agents.md + OpenSpec), the kit's ready-made prompts; all agent-facing content in English.
- **Onboarding any project** = Step 0 (0.1–0.5), a universal core + per-stack plugins.

Open (closed within slices): embeddings (leaning Ollama-local), Confluence ingest (manual → API), the shell
choice (evaluated in C-6).

## Two entities (don't conflate)
1. **The product** (built once): the project-knowledge RAG-MCP + a reused agent shell + per-stack
   plugins/skills. Phases `C-0…C-9` below.
2. **The project onboarding pipeline — "Step 0"** (run on *every* project, idempotent, incremental): makes
   any project "coder-agent-ready" **before** scanning.

## Basis: SDD (reuse, don't reinvent)
- **agents.md** — a thin "map for the agent" in each directory (the nearest file wins).
- **OpenSpec** — `specs/` (source of truth) + `changes/<name>/{proposal, design?, tasks, specs-delta}` +
  `project.md`; the Proposal → Consensus → Implementation → Archive cycle with human gates.
- **Ready-made prompts** from `github.com/sipki-tech/ai-coding-workflow-kit`: `agents-md-generator`,
  `explain-legacy`, `proposal-from-task`, `tasks-from-proposal`, `pr-reviewer`, `skill-generator`.
- **Security:** any external/ingested text (Confluence, docs) = untrusted (prompt-injection /
  tool-poisoning) — handled as untrusted.

## Architecture — 3 context layers (a reminder from REFERENCE)
1. **Spec/doc (SDD, authored, in-repo):** `AGENTS.md` (hierarchical) + `architecture.md` + OpenSpec — thin,
   authoritative, ALWAYS loaded.
2. **RAG (machine-built, our MCP):** code + full Confluence → a relation graph → pulled as a narrow slice on
   demand.
3. **Shell (reuse):** Aider/Continue/Cline, Ask/Edit/Agent modes; model — local 32B by day / Opus for
   indexing + hard tasks.

---

## STEP 0 — universal project onboarding (per project)
Prepare the project so the agent navigates by thin docs + a rich RAG, spending little context.

- **0.1 SDD skeleton.** Generate `AGENTS.md` (root + per-module, hierarchical), `openspec/project.md`
  (conventions), a `openspec/specs/` stub. Reuse `agents-md-generator` (thin, "map not rulebook",
  "Forbidden" zones, a "Before-PR" checklist).
- **0.2 Scan = parser facts + LLM notes bottom-up.** The parser (tree-sitter) gives facts for free and
  exactly: signatures, imports, the call/import graph (= edges). On top — semantic notes hierarchically
  **class → directory → module → project** (`explain-legacy`: purpose/entry-points/flow/dependencies/
  risks/safe-zones, honest "unclear"). **Analyzer tier (model = a switch, local-first):** leaf — a local
  32B; module/project rollup — escalate to Opus (cross-file reasoning). The pipeline is model-agnostic: it
  works on any model, model strength affects richness, not whether it works. Trivial classes (DTOs/getters)
  — parser facts only, no LLM. **Notes → md** (in-repo, = Layer 1).
- **0.3 Ingest external knowledge (Confluence).** Distill the platform-library rules → thin conventions in
  `AGENTS.md`/`project.md`; the full text → into the RAG. (untrusted handling.)
- **0.4 Index → RAG.** The indexer builds a **derived** index over the code + md notes (md is the source, the
  DB is the index): pgvector (semantics) + AGE (graph). Incremental: a class changed → re-read the leaf →
  propagate the rollup upward.
- **0.5 Seed source-of-truth specs.** `openspec/specs/<domain>/spec.md` — capture current behavior.

**Output:** a "coder-agent-ready" project — thin always-on docs + a rich RAG + baseline specs.

## Continuous work cycle (per task, SDD)
`proposal` (`changes/<name>/proposal.md`, prompt `proposal-from-task`) → **human gate (consensus)** →
`tasks.md` (`tasks-from-proposal`) → implementation (Ask/Edit/Agent; local 32B, escalate to Opus for
hard/architectural work) → review (`pr-reviewer`) → archive into `specs/` + reindex the delta.

---

## Product build phases
> **Tooling language (decided):** the core is **Python** (`mcp` SDK, tree-sitter as a universal core,
> embeddings, psycopg for pgvector, the most OSS); the scan is I/O-bound (gated by the model), so runtime
> speed doesn't decide — we pick by ecosystem/development speed. Deep Java semantics (beyond tree-sitter's
> syntax) — an optional **Java sidecar** (Eclipse JDT / JavaParser), a per-stack plugin, not the core.

**State** is the phase-level answer to "what is done"; the slice-level detail (and what each slice
cost us to learn) lives in [STATUS.md](STATUS.md), which is the mutable source of truth. Keep this
column honest at every slice closer — a roadmap that says less than STATUS is worse than no roadmap.

| Phase | What | State | Note |
|---|---|---|---|
| **C-0** | Foundation: this roadmap + a light `architecture.md`; repo scaffold; the DB decision | ✅ done (#1, #2) | own `code.*` schema on an isolated pgvector dev DB; prod rides ai-life's Postgres |
| **C-1** | project-knowledge MCP skeleton: `code.*` schema (pgvector + a plain `code.edge` table; AGE dropped — see architecture.md) + tool contracts (`search_code`, `get_file`, `find_usages`, `get_deps`, `find_convention`, `search_docs`) + a stub indexer | ✅ done | all six contracts are implemented as of D-4 — no stubs left on the surface |
| **C-2** | Code indexer: tree-sitter chunking (universal) → embeddings + graph edges (calls/imports/deps); incremental | ✅ done | deep Java semantics → Java sidecar (C-8); proven on ai-life memory-service + a production Java service |
| **C-3** | Docs/Confluence indexer + linking doc-rule ↔ code | ✅ done (D-1…D-7, #36–#39, #44, #45) | Exported corpus: HTML + `.docx` + text-layer `.pdf` (binaries converted to markdown and archived as Layer 1). OCR for scans, Confluence REST sync, and LLM distillation into `AGENTS.md` (0.3) deliberately deferred |
| **C-4** | Index-time enrichment (Opus): the 0.2 analysis as an automated pass → summaries/relations | ✅ done (C-4a + C-4b + escalation) | "pay once for quality". Leaf notes + bottom-up rollups default to local; escalating a tier is a **config change** — prefix the model `anthropic:` (`[cloud]` extra) and `llm.py` routes it to the Messages API. Not yet driven against the live API |
| **C-5** | Onboarding kit: package Step 0 (0.1–0.5) into a runnable sequence / skills | ○ next up | makes onboarding a new project a command. Needs the owner's real monorepo + Confluence access |
| **C-6** | Shell integration: wire the reused agent (evaluate Aider vs Continue vs Cline) → MCP + Ollama + Opus escalation | ○ planned | the shell choice is still an open decision |
| **C-6a** | **Lifecycle signal to ai-life** (opt-in, default OFF): on session start signal `coder-active` **before** loading the coder model and wait for ai-life's confirmed downshift; on stop / idle-timeout unload the coder model, confirm it is gone, then signal `normal`. Flag + gateway URL + idle TTL from env. | ○ planned | the coder-side half of ai-life LC-4 — without it the shared 64 GB box busts its ceiling |
| **C-7** | SDD-workflow wiring: OpenSpec in-repo + the apply/archive loop + reindex-on-archive | ○ planned | also where the authored `AGENTS.md` layer joins `find_convention` as a *trusted* source |
| **C-8** | Per-stack plugins/skills: Java/Spring/gRPC/GraphQL/Camunda enrichers + skills (`add-graphql-resolver`, `write-camunda-process`, `platform-config-module`) | ○ planned | also the real fix for simple-name matching in `find_usages` / doc linking |
| **C-9** | Security hardening: guards for untrusted ingest | ○ planned | the ingest boundary itself (data-not-instructions, provenance tags) was designed in with C-3, not retrofitted |

## Universality (onboard any project)
The **core** (SDD skeleton, generic chunking, the OpenSpec cycle, the MCP contract) is language-agnostic →
any project onboards. **Depth** (symbol-level usages/deps, framework conventions) is per-stack plugins; a
project without a plugin still onboards on the generic RAG, just shallower.

## Open decisions (closed within slices)
- **A. RAG granularity + retrieval** — a hierarchy (file→class→method) + graph hops, to pull the *connected*
  minimum rather than top-k similar. The heart of "small context → a competent answer".
- **B. Confluence ingest** — manual export/drop first, later a REST API sync.
- **C. DB** — shared ai-life Postgres (schema `code.*`) vs separate. Leaning shared.
- **D. Shell** — Aider vs Continue vs Cline (evaluated in C-6).
- **E. Embeddings** — local Ollama (`nomic-embed-text`/`bge-m3`) vs a dedicated model.
- **F. Community-clustering layer (deferred, evaluate-later).** An **embedding-free** pass that clusters the
  existing `code.edge` graph into topological communities (Leiden via `graspologic` / `python-igraph`) and
  writes one summary note per community — a structural complement to the enrichment rollup (C-4): it carves
  the project into modules by call/import topology rather than by folder, and gives "global" questions a
  cluster-summary to hit. Highest value on the **docs/Confluence** side (C-3) and for module-level rollup.
  Runs on edges we already have — **no vector, no AGE, no new store**. *Prior art:* **Graphify**
  (`Graphify-Labs/graphify`, `graphifyy` on PyPI) does exactly this (Leiden over a tree-sitter graph) —
  mine it for the **recipe + the OSS clustering lib**, do **not** adopt it wholesale: it is graph-only
  (no vector), NetworkX/JSON-stored, and leans on cloud Claude/GPT for the semantic pass — all three clash
  with our vector-first (§3), shared-pgvector, and local-first (§2.3) locks. So: reuse Leiden, keep our
  pipeline.

## Order
First **the Step-0 pipeline works** (C-0…C-5: MCP + indexers + enrichment + onboarding kit), then **the shell
+ the SDD loop** (C-6…C-7), then **per-stack plugins + security** (C-8…C-9). The first test subject for
onboarding is your Java 11 / SB 2.6.4 / gRPC / GraphQL / Camunda monorepo.
