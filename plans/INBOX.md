# INBOX — findings from using the thing, awaiting triage

Raw observations from real use, **already scrubbed of identity**, waiting to become a slice, a
defect entry in [STATUS.md](STATUS.md), or a "no, and here is why". This file is the queue; nothing
here is a commitment.

**Why this exists.** The most valuable input this project gets is a session where retrieval *failed*
— a wrong hit, a tool the shell did not reach for, a page the parser mangled. That signal is
perishable: by the next session it has become "something was a bit off". Capturing it costs seconds;
reconstructing it costs the session it came from.

## How an item gets here

```
notes/inbox.md          (LOCAL, gitignored — raw, may name anything)
   │  scrub: scripts/scrub-file.ps1 notes\inbox.md   → shows what must be redacted
   ▼
plans/INBOX.md          (COMMITTED — identity-free, English, syncs between machines)
   │  triage together
   ▼
a slice (branch → PR) · a STATUS "Known defect" · a roadmap item · or a documented "no"
```

The split is the trust boundary from [`architecture.md`](../architecture.md) §Security applied to
our own notes: **capture must be frictionless, and this repository is public.** Those two cannot
share a file. Raw capture stays on the machine where it happened and never syncs; only the scrubbed
form travels. If an item cannot be stated without naming a system, state the *shape* — "a class
whose name matched a wiki term in three unrelated pages" says everything the fix needs.

## Writing an item so it survives the trip

Four lines, because a finding without them is unactionable a week later:

- **What I was doing** — the task, not the tool ("tracing where a retry limit is configured").
- **What happened** — the observable, with the query if it was a retrieval miss.
- **What I expected** — otherwise "wrong" is unfalsifiable.
- **Guess** (optional) — worth recording even when wrong; a wrong guess narrows the search.

Tag it `[retrieval]`, `[docs]`, `[shell]`, `[setup]`, `[perf]` or `[idea]` so a batch can be triaged
by theme rather than one at a time.

---

## Open

*(empty — the first entries will come from the work machine)*

<!--
Template — copy, fill, delete this comment's siblings as they are promoted:

### [retrieval] Short title
- **Doing:** …
- **Happened:** … (query: "…")
- **Expected:** …
- **Guess:** …
- *Captured: YYYY-MM-DD*
-->

## Promoted

*(items that became a slice or a defect entry, with where they went — kept so a rejected idea does
not come back as a new one every three weeks)*
