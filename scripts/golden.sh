#!/usr/bin/env bash
# Run the golden lanes (retrieval + notes + rollups + docs) against a REAL Ollama + pgvector. Opt-in (not in unit CI).
# Brings up the dev DB, ensures the embed + notes + rollup models, migrates, then runs the `golden` marker.
# NOTE: golden works on a clean slate — it leaves the dev DB holding only the fixture (re-index after).
# Extra args are forwarded to pytest, so you can run a subset:
#   scripts/golden.sh tests/test_golden_retrieval.py        # one lane
#   scripts/golden.sh -k rollup                             # by name
# No args = all golden-marked tests.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose -f infra/docker-compose.yml up -d
uv sync --extra dev --extra index --extra docs
uv run python -m code_context.dev migrate
ollama pull "${CODE_CONTEXT_EMBED_MODEL:-nomic-embed-text}" >/dev/null
ollama pull "${CODE_CONTEXT_NOTES_MODEL:-qwen3:8b}" >/dev/null  # the notes lane drives a real analyzer
ollama pull "${CODE_CONTEXT_ROLLUP_MODEL:-qwen3:8b}" >/dev/null  # the rollup lane drives the rollup tier

uv run pytest -m golden -v "$@"
