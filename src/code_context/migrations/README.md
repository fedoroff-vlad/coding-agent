# migrations

DB schema evolution for the `code.*` derived index. One **unified Python style** for every new
migration — raw SQL over yoyo. It deliberately does **not** mirror ai-life's Liquibase/YAML (different
ecosystem, no need); keep it consistent with the template below.

**The pattern — don't reinvent it per change:**

- **Tool:** [yoyo-migrations](https://ollycope.com/software/yoyo/latest/), raw SQL over our psycopg3
  (`postgresql+psycopg://`, no ORM, no extra driver). Applied by `code_context.db.migrate()`.
- **One file per change:** `NNNN_short_name.sql`, zero-padded, ordered by filename
  (`0001_initial_schema.sql`, `0002_add_x.sql`, …). Forward-only; write idempotent DDL
  (`IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`) so a file is safe on a partially-migrated DB.
- **Apply:** `uv run python -m code_context.dev migrate` (also run by `scripts/start-*`). yoyo records
  applied ids in `_yoyo_migration`, so re-runs are no-ops.
- **Rollback (optional):** add a sibling `NNNN_short_name.rollback.sql`.

## Style — every new migration looks like this

Copy this shape. Raw SQL only:

```sql
-- <what this migration does, one line>.
-- <why, if it isn't obvious — the rationale lives here, in the file>.
-- Idempotent: safe to re-apply on a partially-migrated DB.

ALTER TABLE code.fragment ADD COLUMN IF NOT EXISTS visibility text NOT NULL DEFAULT 'public';
```

Rules (all migrations, no exceptions):

- **One migration = one logical change.** Never edit a committed migration — add the next `NNNN`.
  **This includes comments.** A committed migration describes the schema *as it was created*, and
  every enum-ish comment in it (`kind`, `source`) will drift as the vocabulary grows. Editing them
  in place is how a "harmless" change to an applied file becomes routine — so the initial schema
  now points at [`architecture.md`](../../../architecture.md) instead of listing values, and there
  is nothing left in it worth touching. If you catch yourself editing an applied migration, the
  answer is a new one or a doc change, never a tidy-up.
- **Header comment**: one line of *what*, plus *why* when it isn't obvious.
- **Idempotent DDL** (`IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`).
- **Fully-qualify** objects (`code.<table>`) — this schema is ours alone.
- Keep `vector(N)` in sync with `CODE_CONTEXT_EMBED_DIM`.

## When to migrate vs rebuild

`code.*` is a **derived, rebuildable** index (source of truth = the code/md). So:

- **Additive change** (new column/index) → write a migration. This preserves the *embeddings*, which
  are the expensive part (minutes–hours to recompute).
- **Incompatible change** (retype/rename/drop that would need a backfill) → it's usually cheaper to
  `DROP SCHEMA code CASCADE`, migrate from scratch, and **re-index**. Don't over-engineer a data
  migration for a store you can regenerate.

## Notes

- The `code.*` schema is owned entirely by this project. On the shared ai-life Postgres it coexists with
  ai-life's Liquibase-managed schemas (disjoint schemas, disjoint tracking tables) — they don't conflict.
- Keep `vector(N)` in the schema in sync with `CODE_CONTEXT_EMBED_DIM`.
