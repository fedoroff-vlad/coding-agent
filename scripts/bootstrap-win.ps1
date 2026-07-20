# One-command Windows setup for coding-agent. Idempotent - safe to re-run.
#   .\scripts\bootstrap-win.ps1                        # everything (pulls models - tens of GB)
#   $env:SKIP_MODELS = '1'; .\scripts\bootstrap-win.ps1 # tools only
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

# 4. Models - all of them (running is on demand). Skip with SKIP_MODELS=1.
if ($env:SKIP_MODELS -ne '1') {
    Write-Host ">> pulling models (tens of GB; set `$env:SKIP_MODELS='1' to skip)..."
    & "$PSScriptRoot\pull-models.ps1"
}

Write-Host "`nOK coding-agent ready. Launch a dev session:  .\scripts\start-win.ps1"
