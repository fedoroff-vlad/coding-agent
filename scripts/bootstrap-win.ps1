# One-command Windows setup for coding-agent. Idempotent - safe to re-run.
# Installs only the coding-agent toolchain (winget-packages.json) - not a general workstation set.
#   .\scripts\bootstrap-win.ps1                          # tools + embedding model (this box indexes; the coder runs on the Mac)
#   $env:PULL_CODER = '1';  .\scripts\bootstrap-win.ps1  # also pull the ~19 GB local coder (rarely wanted on a CPU-only box)
#   $env:SKIP_MODELS = '1'; .\scripts\bootstrap-win.ps1  # tools only
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

# 1. winget (ships with Windows 11 as "App Installer").
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
}

# 2. Tools + apps (installs only what's missing).
Write-Host ">> winget import..."
winget import -i winget-packages.json --accept-source-agreements --accept-package-agreements --ignore-unavailable

# Refresh PATH so tools just installed (uv, ollama, ...) are usable in this same session.
$env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path', 'User')

# 3. Python env from the lockfile (uv provisions Python 3.13).
Write-Host ">> uv sync..."
uv sync --extra dev --extra index --extra docs

# 4. Models. This box is index/search only (CPU-only VDI, no GPU) - the coder runs on the Mac, so
#    by default pull ONLY the embedding model (~275 MB), not the ~19 GB coder. PULL_CODER=1 to add it.
if ($env:SKIP_MODELS -ne '1') {
    if ($env:PULL_CODER -eq '1') {
        Write-Host ">> pulling full model set (incl. ~19 GB coder; set `$env:SKIP_MODELS='1' to skip)..."
        & "$PSScriptRoot\pull-models.ps1"
    } else {
        Write-Host ">> pulling embedding model only (coder runs on the Mac; set `$env:PULL_CODER='1' for the full set)..."
        & "$PSScriptRoot\pull-models.ps1" -EmbeddingsOnly
    }
}

Write-Host "`nOK coding-agent ready. Launch a dev session:  .\scripts\start-win.ps1"
