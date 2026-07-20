-- Initial code-context derived index: schema + fragment (pgvector) + edge (relation graph).
--
-- md/code is the source of truth; this DB is a rebuildable DERIVED index. Graph is a plain relational
-- edge table (find_usages/get_deps are 1-hop joins) — Apache AGE is NOT used (architecture.md §Storage).
-- Statements are idempotent (IF NOT EXISTS) so this is safe to apply on a pre-existing schema.

CREATE SCHEMA IF NOT EXISTS code;
CREATE EXTENSION IF NOT EXISTS vector;

-- A code or note fragment: the unit that retrieval returns.
CREATE TABLE IF NOT EXISTS code.fragment (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repo         text NOT NULL,
    path         text NOT NULL,
    kind         text NOT NULL,   -- free text; the live vocabulary is in architecture.md, not here
    symbol       text,            -- fully-qualified symbol name, where applicable
    signature    text,
    line_start   int,
    line_end     int,
    lang         text,
    source       text NOT NULL DEFAULT 'facts',  -- free text; vocabulary in architecture.md
    content      text,            -- code slice or note body (what search returns)
    embedding    vector(768),     -- nomic-embed-text; keep in sync with CODE_CONTEXT_EMBED_DIM
    content_hash text,            -- for incremental re-index (skip unchanged fragments)
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (repo, path, kind, symbol)
);

CREATE INDEX IF NOT EXISTS fragment_repo_path_idx ON code.fragment (repo, path);
CREATE INDEX IF NOT EXISTS fragment_embedding_idx
    ON code.fragment USING hnsw (embedding vector_cosine_ops);

-- A directed edge between fragments (the parser-derived call/import/dep graph).
-- dst_id may be NULL when the target is external or generated code not (yet) indexed; the raw
-- target name is then kept in dst_symbol so the edge is still queryable and resolvable later.
CREATE TABLE IF NOT EXISTS code.edge (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    src_id     bigint NOT NULL REFERENCES code.fragment(id) ON DELETE CASCADE,
    dst_id     bigint REFERENCES code.fragment(id) ON DELETE CASCADE,
    dst_symbol text,            -- set when the target is not (yet) an indexed fragment
    kind       text NOT NULL,   -- free text; the live vocabulary is in architecture.md, not here
    UNIQUE (src_id, dst_id, dst_symbol, kind)
);

CREATE INDEX IF NOT EXISTS edge_src_idx ON code.edge (src_id, kind);
CREATE INDEX IF NOT EXISTS edge_dst_idx ON code.edge (dst_id, kind);
CREATE INDEX IF NOT EXISTS edge_dst_symbol_idx ON code.edge (dst_symbol)
    WHERE dst_symbol IS NOT NULL;
