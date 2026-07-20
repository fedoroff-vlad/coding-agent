"""Runtime configuration for code-context.

All settings are env-overridable with the ``CODE_CONTEXT_`` prefix (or a local ``.env``).
Defaults target local development on the current workstation (Windows box with Ollama on
localhost:11434); in production the same knobs point at the Mac + the shared ai-life Postgres.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODE_CONTEXT_", env_file=".env", extra="ignore"
    )

    # Postgres — the derived index lives in schema ``code.*``.
    # Dev default = the isolated pgvector container (infra/docker-compose.yml, host port 5433).
    # Prod overrides this to the shared ai-life instance via CODE_CONTEXT_DB_DSN.
    db_dsn: str = "postgresql://dev:dev@localhost:5433/code_context"
    db_schema: str = "code"

    # Ollama inference engine. Dev: the Windows box. Prod: the Mac (coder-32B / nomic-embed-text).
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 768  # keep in sync with vector(N) in migrations/0001_initial_schema.sql

    # Index-time enrichment (C-4): the analyzer that writes LLM notes over parser facts.
    # Default = the real coder (prod, on the Mac); dev on the Windows box overrides to a small
    # local model via CODE_CONTEXT_NOTES_MODEL. Model-agnostic — richness varies, not whether it
    # runs (REFERENCE §4.2). Keep the default in the pull-models set (drift-lint enforces it).
    notes_model: str = "qwen3-coder:30b"
    notes_num_ctx: int = 8192  # a leaf note sees one class' facts — RAG-first, no giant window.
    notes_timeout_s: int = 180
    # Rollup tier (C-4b): the directory→module→project notes synthesized bottom-up over the leaf
    # notes. The roadmap escalates this to a STRONG model (Opus) for cross-file reasoning: prefix
    # the model with "anthropic:" (e.g. anthropic:claude-opus-4-8) and llm.py routes the call to
    # the cloud tier instead of Ollama. The default stays local-capable — escalation costs money
    # and needs a key — and the pipeline stays model-agnostic. Also drift-lint-pinned (local only).
    rollup_model: str = "qwen3-coder:30b"
    # A rollup prompt aggregates every child note of a directory, so it is far larger than a leaf's
    # single class — and the upper tiers (module / project) are the largest of all. Budget for that
    # separately: reusing the leaf numbers truncates the prompt and times the call out on a slow
    # (CPU-only) engine, which kills the run exactly at the most valuable tier.
    rollup_num_ctx: int = 32768
    rollup_timeout_s: int = 900
    # Output ceiling for the cloud tier (an "anthropic:" model). A note is a few sentences, but
    # adaptive thinking draws from the same budget, so this is sized for the reasoning, not the
    # prose. The local tier has no equivalent knob — there the window (num_ctx) is the limit.
    cloud_max_tokens: int = 16000
    # Filenames that mark a directory as a build "module" (→ kind='module'); else 'directory'.
    module_markers: tuple[str, ...] = ("pom.xml", "build.gradle", "build.gradle.kts")
    # Where md notes (Layer 1, md-as-source) are written. Empty → "<repo>/.code-context/notes";
    # set it to redirect notes off a stand-in repo (e.g. indexing ai-life) into a scratch tree.
    notes_root: str = ""

    # Retrieval: the target budget a single tool call returns (~8–20k tokens of context).
    search_default_limit: int = 10
    # The repo every tool scopes to when the caller passes none. One index can hold several repos
    # (plus their docs corpora), and an unscoped query mixes them — which is a wrong answer, not a
    # wide one. Empty = search everything indexed, which is only right for a deliberate cross-repo
    # index; a session pinned to one project should set this.
    default_repo: str = ""

    # Docs ingest (C-3). A section larger than this is split into parts that keep the same heading
    # path — one giant wiki table would otherwise produce a fragment too large to embed usefully.
    docs_max_chars: int = 4000
    # Where a converted binary document (.docx → md, D-6) is archived. Empty → "<docs>/.code-context/md".
    # The markdown is the Layer-1 record: the source is neither greppable nor diffable, so without
    # this file the only readable form of the document is rows in the index. Same rule as notes_root.
    docs_md_root: str = ""

    # Observability (architecture.md §Observability). Events go to stderr — stdout is the MCP
    # protocol channel. Level changes how MUCH is logged (INFO = run start/finish + counts +
    # warnings; DEBUG adds per-node events), never WHETHER payloads are: prompts, class bodies and
    # note text are customer source code and are never logged, at any level, by design.
    log_level: str = "INFO"
    log_format: str = "json"  # json = JSON Lines (ship to Elasticsearch/Kibana); text = human-readable


settings = Settings()
