#!/usr/bin/env bash
# One-command launch of a coding-agent dev session. Idempotent — safe to re-run.
# Brings up the inference engine + the dev database + the Python env, ready for the MCP server.
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. uv (the Python toolchain; bootstrap installs it, but a fresh box may skip straight to here).
if ! command -v uv >/dev/null 2>&1; then
    echo ">> uv not found — installing (brew)..."
    brew install uv
    command -v uv >/dev/null 2>&1 || {
        echo "uv still not on PATH after install — open a new shell and re-run, or run ./scripts/bootstrap-mac.sh." >&2
        exit 1
    }
fi

# 2. Inference engine.
brew services start ollama >/dev/null 2>&1 || true

# 3. Dev database (isolated pgvector, host :5433).
docker compose -f infra/docker-compose.yml up -d

# 4. Python env + DB migrations.
uv sync --extra dev --extra index --extra docs
uv run python -m code_context.dev migrate

echo ""
echo "✅ code-context dev session up."
echo "   Run the MCP server:  uv run code-context"
echo "   Stop the database:   docker compose -f infra/docker-compose.yml down"
