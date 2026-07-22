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
    # OpenAI-dialect tier: any analyzer model prefixed "openai:" is sent to this base URL as a
    # /chat/completions call. Written for a company's own gateway (one URL, several models behind
    # it) — the same dialect ai-life calls "openai-compatible". Must include the version prefix,
    # e.g. https://llm.example.internal/v1.
    # The API KEY IS DELIBERATELY NOT A SETTING — see llm.py: it is read from the environment at
    # call time so a secret never lands in this object, which is one print(settings) from a log.
    openai_base_url: str = ""
    # Send "reasoning_effort": "none" on every OpenAI-dialect call. A thinking model (qwen3 and
    # kin) otherwise spends the whole budget reasoning about a two-sentence note. ai-life paid to
    # learn that the "/no_think" prompt tag does NOT work through an OpenAI /v1 endpoint — only
    # this body field does (platform/llm-gateway/README.md §LLM_SUPPRESS_THINKING). Off by
    # default: a gateway that rejects unknown fields would fail every call.
    openai_suppress_thinking: bool = False
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

    # Confluence REST sync (roadmap decision B, the automatic half of the docs front). The site URL
    # as you see it in a browser — e.g. https://wiki.example.internal, or https://<site>.atlassian.net
    # for Cloud. NEVER commit the real one: an internal hostname identifies an employer as surely as
    # a name does (scrub-identity / .private-terms).
    confluence_base_url: str = ""
    # The REST root under the site. Data Center/Server serves /rest/api; Cloud serves /wiki/rest/api.
    # Explicit rather than sniffed: probing would fail confusingly behind SSO, and a wrong guess
    # looks like an auth problem.
    confluence_api_path: str = "rest/api"
    # Set for Cloud ONLY: it makes the client send Basic (email + API token) instead of a Bearer
    # token. Empty = a Data Center Personal Access Token. The token itself is env-only
    # (CODE_CONTEXT_CONFLUENCE_TOKEN) and deliberately not a field here — see the OpenAI key.
    confluence_email: str = ""
    # 'view' = macros RENDERED (a code macro arrives as <pre>, a table macro as <table>), which is
    # the shape D-1's parser was written against, since an export is rendered too. 'storage' is the
    # raw editor format — use it only if a site refuses view rendering, and expect macros to arrive
    # as <ac:structured-macro> the parser cannot see into.
    confluence_body_format: str = "view"
    confluence_page_limit: int = 50   # pagination window; the API caps it well below a whole space
    confluence_timeout_s: int = 60

    # Docs ingest (C-3). A section larger than this is split into parts that keep the same heading
    # path — one giant wiki table would otherwise produce a fragment too large to embed usefully.
    docs_max_chars: int = 4000
    # Where a converted binary document (.docx → md, D-6) is archived. Empty → "<docs>/.code-context/md".
    # The markdown is the Layer-1 record: the source is neither greppable nor diffable, so without
    # this file the only readable form of the document is rows in the index. Same rule as notes_root.
    docs_md_root: str = ""

    # Lifecycle signal to ai-life (C-6a). The two contours share one Mac and one Ollama, and the
    # GPU working set tops out around 48 GB — so before this repo loads its coder model, ai-life
    # must have *finished* downshifting its own. Opt-in and OFF by default: either contour has to
    # run standalone unaffected, and switching this on is a statement that both are co-resident.
    lifecycle_enabled: bool = False
    # ai-life's llm-gateway, which owns the model profile (its LC-4 /v1/model-profile endpoint).
    # Published on the host at 8081 by ai-life's compose, so the default is the same-Mac case.
    lifecycle_gateway_url: str = "http://localhost:8081"
    # How long to wait for ai-life to confirm the downshift. It has to evict a 32B model, so this
    # is a model-unload budget, not an HTTP round trip. A timeout FAILS the call: proceeding would
    # load our model on top of theirs, which is the exact crash the handshake exists to prevent.
    lifecycle_signal_timeout_s: int = 180
    # How long to wait for OUR model to actually leave Ollama on the way back down, before we let
    # ai-life restore its big one. Same reasoning, mirrored.
    lifecycle_unload_timeout_s: int = 120
    # Release the shared engine after this long with no analyzer call — an indexing run that ended
    # (or a shell left open overnight) should not hold ai-life on the small model forever. The next
    # analyzer call re-acquires. 0 disables the timer (release then only happens at run end).
    lifecycle_idle_ttl_s: int = 900

    # Observability (architecture.md §Observability). Events go to stderr — stdout is the MCP
    # protocol channel. Level changes how MUCH is logged (INFO = run start/finish + counts +
    # warnings; DEBUG adds per-node events), never WHETHER payloads are: prompts, class bodies and
    # note text are customer source code and are never logged, at any level, by design.
    log_level: str = "INFO"
    log_format: str = "json"  # json = JSON Lines (ship to Elasticsearch/Kibana); text = human-readable


settings = Settings()
