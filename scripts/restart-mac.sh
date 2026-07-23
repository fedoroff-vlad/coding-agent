#!/usr/bin/env bash
# Refresh the running coding-agent dev environment after making changes — the fast inner loop
# between `git pull` (new migrations / deps / a changed compose file) and the next dev session.
# Idempotent. Restarts the Docker services (pgvector + backup sidecar), re-syncs the Python env,
# and re-applies DB migrations. The macOS mirror of scripts/restart-win.ps1.
#
#   ./scripts/restart-mac.sh                   # restart containers, uv sync, migrate
#   ./scripts/restart-mac.sh --clean           # ALSO wipe the DB volume first -> empty index (re-index after)
#   ./scripts/restart-mac.sh --reindex ~/repo  # ...then re-index that repo
#
# What it does NOT do, on purpose: the MCP server (`uv run code-context`) is a HOST process that
# opencode spawns per session, not a container — so a code change to the server is picked up by
# restarting OPENCODE, which this script cannot reach. This refreshes the infrastructure the server
# talks to (DB + Python env + schema), not opencode's MCP child. See start-mac.sh for a cold start.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="infra/docker-compose.yml"
CLEAN=0
REINDEX=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean) CLEAN=1; shift ;;
    --reindex) REINDEX="${2:-}"; [[ -n "$REINDEX" ]] || { echo "--reindex needs a repo path" >&2; exit 1; }; shift 2 ;;
    *) echo "unknown argument: $1 (use --clean and/or --reindex <repo>)" >&2; exit 1 ;;
  esac
done

# 1. Docker must be running.
if ! docker info >/dev/null 2>&1; then
  echo "Docker isn't running — start Docker Desktop, then re-run." >&2
  exit 1
fi

# 2. The containers.
if [[ "$CLEAN" == "1" ]]; then
  # Wipe the volume for a clean slate (a schema experiment, a corrupted index). The index is
  # DERIVED — md/code is the source, `dev index` regenerates it — so this loses only rebuildable
  # data, but you must re-index afterwards (--reindex, or `dev index <repo>` by hand).
  echo ">> down -v (wiping the DB volume)..."
  docker compose -f "$COMPOSE" down -v
  echo ">> up -d (fresh pgvector)..."
  docker compose -f "$COMPOSE" up -d
else
  # --force-recreate: an actual restart that also applies a changed compose definition, while the
  # named volume (and therefore the index) survives. A plain `restart` would not pick up an edit.
  echo ">> restarting containers (up -d --force-recreate; volume/index preserved)..."
  docker compose -f "$COMPOSE" up -d --force-recreate
fi

# 3. Python env + migrations — a pull may have added either. uv sync is a no-op when nothing moved.
echo ">> uv sync..."
uv sync --extra dev --extra index --extra docs
echo ">> migrate..."
uv run python -m code_context.dev migrate

# 4. Optional re-index (required after --clean; handy after a big branch change otherwise).
if [[ -n "$REINDEX" ]]; then
  [[ -d "$REINDEX" ]] || { echo "--reindex '$REINDEX' does not exist." >&2; exit 1; }
  echo ">> indexing $REINDEX..."
  uv run python -m code_context.dev index "$REINDEX"
fi

echo ""
echo "✅ dev environment refreshed."
if [[ -z "$REINDEX" && "$CLEAN" == "1" ]]; then
  echo "   The volume was wiped — re-index before use:  uv run python -m code_context.dev index <repo>"
fi
echo "   Changed the MCP server code? Restart opencode so it respawns 'uv run code-context'."
