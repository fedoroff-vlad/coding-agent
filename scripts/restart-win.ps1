# Refresh the running coding-agent dev environment after making changes - the fast inner loop
# between `git pull` (new migrations / deps / a changed compose file) and the next dev session.
# Idempotent. Restarts the Docker services (pgvector + backup sidecar), re-syncs the Python env,
# and re-applies DB migrations.
#
#   .\scripts\restart-win.ps1                    # restart containers, uv sync, migrate
#   .\scripts\restart-win.ps1 -Clean             # ALSO wipe the DB volume first -> empty index (re-index after)
#   .\scripts\restart-win.ps1 -Reindex C:\repo   # ...then re-index that repo
#
# What it does NOT do, on purpose: the MCP server (`uv run code-context`) is a HOST process that
# opencode spawns per session, not a container - so a code change to the server is picked up by
# restarting OPENCODE, which this script cannot reach. This refreshes the infrastructure the server
# talks to (DB + Python env + schema), not opencode's MCP child. See start-win.ps1 for a cold start.
param(
    [switch]$Clean,
    [string]$Reindex
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

$compose = 'infra/docker-compose.yml'

# Pick up tools installed by bootstrap into a shell that predates them (mirrors start-win.ps1).
$env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path', 'User')

# 1. Docker must be running (Docker Desktop). *> $null + $LASTEXITCODE, not try/catch: `docker info`
#    writes to stderr on a down daemon, which ErrorActionPreference='Stop' would turn terminating.
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker isn't running - start Docker Desktop, then re-run."
}

# 2. The containers.
if ($Clean) {
    # Wipe the volume for a clean slate (a schema experiment, a corrupted index). The index is
    # DERIVED - md/code is the source, `dev index` regenerates it - so this loses only rebuildable
    # data, but you must re-index afterwards (-Reindex, or `dev index <repo>` by hand).
    Write-Host ">> down -v (wiping the DB volume)..."
    docker compose -f $compose down -v
    Write-Host ">> up -d (fresh pgvector)..."
    docker compose -f $compose up -d
} else {
    # --force-recreate: an actual restart that also applies a changed compose definition, while the
    # named volume (and therefore the index) survives. A plain `restart` would not pick up an edit.
    Write-Host ">> restarting containers (up -d --force-recreate; volume/index preserved)..."
    docker compose -f $compose up -d --force-recreate
}

# 3. Python env + migrations - a pull may have added either. uv sync is a no-op when nothing moved.
Write-Host ">> uv sync..."
uv sync --extra dev --extra index --extra docs
Write-Host ">> migrate..."
uv run python -m code_context.dev migrate

# 4. Optional re-index (required after -Clean; handy after a big branch change otherwise).
if ($Reindex) {
    if (-not (Test-Path $Reindex)) { throw "-Reindex '$Reindex' does not exist." }
    $Reindex = (Resolve-Path $Reindex).Path
    Write-Host ">> indexing $Reindex..."
    uv run python -m code_context.dev index $Reindex
}

Write-Host "`nOK dev environment refreshed."
if (-not $Reindex -and $Clean) {
    Write-Host "   The volume was wiped - re-index before use:  uv run python -m code_context.dev index <repo>"
}
Write-Host "   Changed the MCP server code? Restart opencode so it respawns 'uv run code-context'."
