# One-command launch of a coding-agent dev session on Windows. Idempotent - safe to re-run.
# Brings up the inference engine + the dev database + the Python env, ready for the MCP server.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

# Pick up tools installed by a previous run / by bootstrap into a shell that predates them.
function Sync-PathFromMachine {
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path', 'User')
}
Sync-PathFromMachine

# 1. Docker must be running (Docker Desktop).
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker isn't running - start Docker Desktop, then re-run."
}

# 2. uv (the Python toolchain; bootstrap installs it, but a fresh box may skip straight to here).
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host ">> uv not found - installing (winget astral-sh.uv)..."
    winget install --id astral-sh.uv --accept-source-agreements --accept-package-agreements --silent
    Sync-PathFromMachine  # winget edits the persisted PATH, not this shell's copy
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv still not on PATH after install - open a new shell and re-run, or run .\scripts\bootstrap-win.ps1."
    }
}

# 3. Inference engine (symmetric with start-mac.sh's `brew services start ollama`).
if (-not (Get-Process ollama -ErrorAction SilentlyContinue)) {
    $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $ollama) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
    if (Test-Path $ollama) {
        Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
    } else {
        Write-Host "!! Ollama not found - embeddings and notes will fail. Run .\scripts\bootstrap-win.ps1."
    }
}

# 4. Dev database (isolated pgvector, host :5433).
docker compose -f infra/docker-compose.yml up -d

# 5. Python env + DB migrations.
uv sync --extra dev --extra index --extra docs
uv run python -m code_context.dev migrate

Write-Host "`nOK code-context dev session up."
Write-Host "   Run the MCP server:  uv run code-context"
Write-Host "   Stop the database:   docker compose -f infra/docker-compose.yml down"
