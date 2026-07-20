# Coding Agent — Reference

> Source of truth for the coding contour. Read by the coding AI agent as context.
> Level: strategy + architecture. No code here — code lives in the corresponding plan files.

---

## 1. Strategy (locked)

### 1.1. Two models + swap (decided 2026-07-10, NOT one shared model)
A single local inference engine (Ollama) on the host serves both contours, but there are **two models**,
and at most one large (30–32B) model is resident at a time:

- **ai-life** — general model `qwen3:32b` (household: conversation, reasoning, coach).
- **coding** — `qwen3-coder:30b` (Qwen3-Coder-Flash, MoE — code-specialised, current SOTA in class).

During a coding session ai-life **downshifts to `qwen3:14b`**, freeing memory for the 30B coder; after the
session it returns to 32B. Two large models are **never** resident at once — that keeps the plan within 64 GB.
The downshift is an **opt-in** coupling (default off — see the swap mechanism below); the tags above are
*our* current pair, read from env on both sides, not baked in.

*Why not one shared model:* a code model makes a noticeably weaker household assistant, and a general model
makes a weaker coder; so each contour gets its own. The contours are still separated by prompts, skills, and
MCP — but **not** by economising on the model.

**The swap lives in ai-life** (`llm-gateway` model-manager — slice LC-4 in `ai-life/plans/lifecycle.md`);
**emitting the signal is ours — slice C-6a** ([`architecture.md`](../architecture.md) §Contours). The whole
coupling is **opt-in, default OFF**, and each side reads its model tags from env: either contour must run
standalone unaffected, and someone else's pair will be different models entirely.

A clean unload of the outgoing model is mandatory, and the **order is load-bearing — the opposite of the
intuitive one**: signal `coder-active` → wait for ai-life's *confirmed* eviction → **only then** load the
coder (and symmetrically on the way out: unload ours, confirm, then signal `normal`). Loading first and
signalling after leaves three models in memory for a moment and blows past 64 GB.

### 1.2. Load profiles
- **Baseline (24/7):** the ai-life docker stack + a resident `qwen3:32b`. The household assistant, always on.
- **Coding session (on demand):** the coding-agent infra comes up via `docker compose up` and runs on
  **`qwen3-coder:30b`**; ai-life sits on `qwen3:14b` meanwhile. After work — `docker compose down`,
  ai-life returns to 32B.
- Non-key ai-life agents (stylist, chef, etc.) — dormant, started on demand (hot/cold, see
  `ai-life/plans/lifecycle.md`).

### 1.3. Target hardware
**Mac Studio M4 Max, 64 GB — decided (one Mac, no separate GPU node: we don't carry extra infra).**
Sufficient for the whole strategy given RAG-first (see §3) + the two-model swap with a clean unload (§1.1):
peak during an active coding session ≈ 45–50 GB (OS + ai-life hot JVMs + backing services + 14B + coder-30B),
~14 GB headroom. More memory (128 GB) would only be needed to hold two large models at once (no downshift) or a giant
context window in memory — and we don't do that.

---

## 2. Quality expectations (important, so we don't build illusions)

### 2.1. What a local 32B covers well (~80% of coding)
Routine, private, no tokens:
- generating tests from a signature,
- method-level refactoring,
- routine vulnerability scanning,
- boilerplate,
- explaining code.

### 2.2. What a local 32B does NOT cover
The gap versus paid frontier models (Opus / GPT) is felt on:
- architectural reasoning across the whole project,
- subtle bugs in unfamiliar code,
- long multi-file refactors that require understanding every relationship.

**A local (on-Mac) equivalent of a frontier model does not exist.** Open frontier-class models
(GLM-5.2, DeepSeek V4, Kimi K2, large Qwen) require server-grade multi-GPU hardware.

### 2.3. Three-tier usage model
1. **Local 32B** — private routine + 24/7 ai-life. No tokens.
2. **Cheap open API** (e.g. DeepSeek) — when you need quality above local but not flagship pricing.
3. **Flagship (Opus / GPT)** — only for the genuinely hard, when it's justified.

---

## 3. Context: RAG-first, NOT a giant window (the key architectural decision)

### 3.1. Why NOT 200–400k in the window
Two independent barriers:

- **Memory (physics).** A KV cache at 200–400k on a 32B adds tens of GB. Next to docker and the model on
  64 GB it physically doesn't fit. Realistic: ~32–64k, maybe ~128k with cache compression.
- **Quality.** Even with the memory, the model degrades on a long context ("lost in the middle",
  hallucinations, ignoring part of the context).

Prompts don't fix this — what fixes it is making the long context **unnecessary**.

### 3.2. Solution: retrieval instead of a big window
The goal — each LLM call sees only ~8–20k of relevant context.

Mechanisms:
- **RAG over the codebase** via pgvector (already in the stack). The model doesn't load the whole project —
  it pulls only the needed files/fragments on demand.
- **Agentic decomposition.** Not "read the module and refactor", but: find the right places → work on each in
  a narrow context → assemble the result. Each call sees little, but precisely.
- **MCP tools as "hands in the codebase"** (see §4). The model doesn't hold the project in its head — it asks
  the tools.
- **Task-specific skills** — sharpened procedures for "write a test", "find a vulnerability", "propose a
  refactor", so the model follows a proven template rather than reasoning from scratch.

Careful retrieval + decomposition beat a big window on quality, memory, and speed at once.
**This is what makes the whole plan work on 64 GB.**

---

## 4. Architecture: the `code-context` MCP server

The foundation of the coding contour. One MCP server that gives the agent access to the codebase through
tools, not by loading everything into context. Reused in ai-life too (memory-service on the same pgvector).

### 4.1. Tools (contract)
Six tools, **all implemented** as of C-3/D-4 (the shipped names win where this table once said
`get_dependencies`; live signatures: [`architecture.md`](../architecture.md) §Package layout).

| Tool | Purpose | What it returns (narrow context) |
|---|---|---|
| `search_code` | Vector/semantic search over the **code** index (ingested docs excluded) | Top-N relevant fragments with paths and line ranges |
| `get_file` | Fetch a file or line range precisely | File/fragment contents |
| `find_usages` | Where a symbol (method/class/field) is used | List of usage sites |
| `get_deps` | Dependencies of a module/class (imports, links) | Indexed classes the type imports |
| `search_docs` | Semantic search over the ingested docs corpus | Doc sections + heading path, tagged `source`/`trust` |
| `find_convention` | The documented rules governing the class you are writing | Sections that *name* the class first (the `mentions` join), then semantic hits — same tagged shape |

Principle: tools return the **minimally sufficient** slice, not "everything just in case".

Two properties every tool shares: results are **repo-scoped** (`repo` argument, default
`CODE_CONTEXT_DEFAULT_REPO` — one index holds several projects), and **ingested documentation is
data, never instructions** — it comes back as a distinct `Doc` shape carrying its provenance, so a
consumer can never mistake wiki prose for the codebase's own facts.

### 4.2. Indexing layer (refined 2026-07-10)
A hybrid of "parser facts + an LLM note on top", hierarchical bottom-up. **md is the source, the DB is a
derived index over it** (not two sources of truth).

- **Parser facts (free, exact):** a parser walk over the repo yields signatures, imports, the call/import
  graph, who-uses-whom. This is the fragment skeleton and the **graph edges**. Trivial classes (DTOs/getters)
  stop here — we don't spend an LLM on them.
- **LLM notes, hierarchically:** class → directory → module → project. A semantic note **on top of the facts**
  (anchored to real signatures → no hallucination), only where it adds value.
  - **Analyzer tier (model = a simple switch, local-first):** leaf/class — a local 32B (bounded context,
    handles it); module/project rollup — escalate to a strong model (Opus), since it needs cross-file
    reasoning. **The pipeline is model-agnostic** — it must produce a working index on any model; model
    strength affects the *richness* of the notes, not whether it works. Fully local is the default, with an
    accepted quality ceiling on the higher-level notes.
- **Notes are written as md** (in the repo, git, = Layer 1 / `architecture.md` + per-module docs). The
  **indexer** then builds the derived RAG: code + md notes → **pgvector** (semantics) + a plain **`code.edge`** table (1-hop relation graph). Apache AGE was evaluated and **decided against** — the graph tools are 1-hop joins, so a graph DB isn't warranted (see `architecture.md` §Graph).
- **Incremental:** a class changed → re-read its leaf → propagate the rollup upward.
- Fragment metadata: path, line range, kind (class/method), signature, edge references.

### 4.3. How the agent uses it (flow)
1. A task reaches the agent (e.g. "refactor method X").
2. Via `search_code` / `find_usages` the agent finds the relevant places — it does NOT load the whole module.
3. Via `get_file` it fetches only the needed fragments (~8–20k total).
4. It works in a narrow context, decomposing into sub-steps when needed.
5. It assembles the result.

---

## 5. First template (the next step of work)

Build the `code-context` MCP server as a **skeleton template** with the four tools (§4.1) + a script that
indexes a Java project into pgvector. From there it's cloned per agent domain: vulnerability scanner, test
generator, refactoring agent.

**Tooling language — DECIDED (2026-07-10): Python core + optional Java sidecar.**
- **Core (scanner/indexer/MCP) — Python.** The scan is gated by model calls (I/O-bound), not CPU → the
  runtime speed of Go/Rust doesn't pay off; we pick by ecosystem and development speed. Python gives the `mcp`
  SDK, `tree-sitter`(-languages) as a **universal core** for any language, embeddings, psycopg (pgvector+AGE),
  and the most ready-made OSS. Python tooling targeting Java is exactly "don't couple to the stack".
- **Java sidecar (optional, a per-stack plugin):** if we need *semantics* (full type resolution beyond
  tree-sitter's syntax) — a thin service on Eclipse JDT / JavaParser. A plugin, not the core.

Open decision:
- **Embeddings:** locally via Ollama (`nomic-embed-text` / `bge-m3`) vs a dedicated embed model. Leaning toward
  Ollama-local.

---

## 6. Infrastructure notes

- **Workstation vs server.** A 24/7 inference node can stand separately; you connect to it by URL (Ollama
  listens over HTTP). The IDE is on the work machine (VS Code Remote-SSH / JetBrains Gateway to the server, or
  code local + LLM over the network). The coding infra is on the server, brought up per session.
- **Non-Apple alternative for an inference node:** a PC with a single GPU (24 GB VRAM) + lots of cheap DDR5 —
  sidesteps the Apple unified-memory shortage and fits the networked architecture. Downsides: Linux,
  noise/heat, setup; 24 GB VRAM holds a 32B in Q4 tightly (not a 70B).
- **KV keep_alive:** keep the active model resident (`keep_alive: -1`) to avoid paying for a cold start.
  **But on a swap** (§1.1) the outgoing model must be explicitly unloaded (`keep_alive: 0` / `ollama stop`) —
  otherwise there's no room on 64 GB for the incoming one. So `-1` is for the one currently working, not
  "forever for all".
