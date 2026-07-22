# Plan index — coding-agent

| File | What | Read when |
|---|---|---|
| [REFERENCE.md](REFERENCE.md) | **Strategy + architecture** (source of truth): two models + swap, RAG-first, the `code-context` MCP + its tool contract, the indexing layer (parser facts + bottom-up LLM notes), the Python core + Java sidecar, the three-tier model usage | Any work — to understand the "why" and "how it's built" |
| [roadmap.md](roadmap.md) | **How we build it:** the universal **Step 0** (onboarding any project), the continuous SDD cycle, phases C-0…C-9, the "Locked decisions" block | Planning a slice, "what's next" |
| [STATUS.md](STATUS.md) | **What's in flight / the next slice** (mutable) | Start of a session |
| [INBOX.md](INBOX.md) | **Findings from real use, awaiting triage** — scrubbed of identity; raw capture lives in the gitignored `notes/` | Start of a session; after using the thing at work |

Component-level architecture is now in [`../architecture.md`](../architecture.md) (split out in C-0). It
covers the *what/where* of the code (package layout, storage decision); `REFERENCE.md` keeps the *why*
(§3 RAG, §4 the `code-context` MCP, §6 infra).

## External anchors
- **SDD kit:** `github.com/sipki-tech/ai-coding-workflow-kit` · `agents.md` · OpenSpec.
- **Sibling project** ai-life (`../ai-life`): shares the Mac; the model swap + hot/cold live in its
  `llm-gateway` / `plans/lifecycle.md`.
