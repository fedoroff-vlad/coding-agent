# One-command setup of coding-agent on a WORK machine (Windows), where the LLM is a company
# gateway and the shell is opencode. Idempotent - safe to re-run (re-running is also how you
# refresh the installed skills after a submodule bump).
#
#   .\scripts\work-win.ps1 -Repo C:\path\to\your\monorepo
#   .\scripts\work-win.ps1 -Repo C:\path\to\repo -SkipIndex   # everything but the (slow) indexing
#   .\scripts\work-win.ps1 -WireOnly                          # just opencode: re-register + refresh skills
#
# What it does: dev session (Docker + uv + pgvector + migrations) -> the embed model -> index your
# repo -> register the code-context MCP server in opencode -> install the dev-workflow skills.
#
# Retrieval needs NO analyzer model, so this script never asks for the gateway URL or a key: after
# it finishes you can already search and read code from the shell. Wiring the analyzer (the
# optional `enrich`/`rollup` notes) is the CODE_CONTEXT_OPENAI_* block in .env.example.
#
# Windows-only on purpose: the work machine is Windows. The portable form of every step is the
# manual sequence in README "Use it on a work machine" - keep the two in step.
param(
    [string]$Repo,
    [switch]$SkipIndex,
    # Skip the infrastructure half entirely - no Docker, no model pull, no indexing. For
    # re-registering the MCP server or refreshing the skills after a submodule bump.
    [switch]$WireOnly,
    [string]$DbDsn = 'postgresql://dev:dev@localhost:5433/code_context'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Say($msg) { Write-Host ">> $msg" }

if ($Repo) {
    if (-not (Test-Path $Repo)) { throw "-Repo '$Repo' does not exist." }
    $Repo = (Resolve-Path $Repo).Path   # opencode gets an absolute path, whatever you typed
}

if (-not $WireOnly) {
    # ------------------------------------------------------------ 1. infra + python env
    # start-win.ps1 already owns this half (Docker running, uv present, Ollama up, pgvector up,
    # uv sync, migrate). Reuse it rather than keeping a second copy of the same five steps in step.
    Say 'dev session (Docker, uv, Ollama, pgvector, migrations)...'
    & "$PSScriptRoot\start-win.ps1"

    # ------------------------------------------------------------ 2. the embed model only
    # NOT pull-models.ps1: that pulls the analyzer models too (tens of GB) and a work machine
    # driving a company gateway needs none of them. Embeddings are the one thing that stays local.
    $embed = 'nomic-embed-text'
    # -match on a string ARRAY filters it (returns the matching lines), it does not return a
    # boolean: `-notmatch` here would be truthy whenever any other line differs, i.e. always.
    if (-not ((ollama list) -match [regex]::Escape($embed))) {
        Say "pulling $embed (~274 MB - the only model this setup needs)..."
        ollama pull $embed
    } else {
        Say "$embed already pulled."
    }

    # ------------------------------------------------------------ 3. index the work repo
    if ($Repo -and -not $SkipIndex) {
        Say "indexing $Repo (parser facts + embeddings, no LLM; minutes on a big monorepo)..."
        uv run python -m code_context.dev index $Repo
    }
    if (-not $Repo) {
        Write-Host "!! No -Repo given: skipping the index. The MCP server will answer from an empty"
        Write-Host "   index until you run:  uv run python -m code_context.dev index <path>"
    }
}

# ---------------------------------------------------------------- 4. opencode: the MCP server
$cfgDir = if ($env:XDG_CONFIG_HOME) { Join-Path $env:XDG_CONFIG_HOME 'opencode' }
          else { Join-Path $env:USERPROFILE '.config\opencode' }
$jsonPath  = Join-Path $cfgDir 'opencode.json'
$jsoncPath = Join-Path $cfgDir 'opencode.jsonc'

$envBlock = [ordered]@{ CODE_CONTEXT_DB_DSN = $DbDsn }
if ($Repo) { $envBlock['CODE_CONTEXT_DEFAULT_REPO'] = $Repo }  # one index can hold several repos
$entry = [pscustomobject]@{
    type        = 'local'
    command     = @('uv', 'run', '--directory', $root, 'code-context')
    enabled     = $true
    environment = [pscustomobject]$envBlock
}

if (Test-Path $jsoncPath) {
    # A .jsonc is hand-written and may carry comments; rewriting it through ConvertTo-Json would
    # silently delete them. Print the snippet instead - your file, your edit.
    Write-Host ""
    Write-Host "!! Found $jsoncPath (comments would not survive an automatic rewrite)."
    Write-Host "   Add this entry under its top-level 'mcp' key yourself:"
    Write-Host (@{ 'code-context' = $entry } | ConvertTo-Json -Depth 10)
    $manualEdit = $true
} else {
    if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null }
    if (Test-Path $jsonPath) {
        # Never clobber the provider config that already makes the work model reachable.
        Copy-Item $jsonPath "$jsonPath.bak" -Force
        $cfg = Get-Content $jsonPath -Raw | ConvertFrom-Json
        Say "merging into $jsonPath (backup: opencode.json.bak)"
    } else {
        $cfg = [pscustomobject]@{ '$schema' = 'https://opencode.ai/config.json' }
        Say "creating $jsonPath"
    }
    if (-not $cfg.PSObject.Properties['mcp']) {
        $cfg | Add-Member -NotePropertyName 'mcp' -NotePropertyValue ([pscustomobject]@{})
    }
    if ($cfg.mcp.PSObject.Properties['code-context']) {
        $cfg.mcp.'code-context' = $entry           # idempotent: re-run updates the paths
    } else {
        $cfg.mcp | Add-Member -NotePropertyName 'code-context' -NotePropertyValue $entry
    }
    # .NET writer, not Set-Content: PowerShell 5.1 writes UTF-8 *with* a BOM, and a BOM in front
    # of a JSON document trips strict parsers.
    [System.IO.File]::WriteAllText($jsonPath, ($cfg | ConvertTo-Json -Depth 10))
}

# ---------------------------------------------------------------- 5. opencode: the skills
# opencode only discovers skills in six fixed locations - a submodule under tools/ is not one of
# them, so they have to be installed. Global (not .opencode/ inside your work repo): these are
# your workflow, not that repository's, and they must not end up in its diff.
$src = Join-Path $root 'tools\agent-skills\skills'
$dst = Join-Path $cfgDir 'skills'
if (-not (Test-Path $src) -or -not (Get-ChildItem $src -ErrorAction SilentlyContinue)) {
    Write-Host "!! $src is empty - run: git submodule update --init --recursive"
} else {
    if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Force -Path $dst | Out-Null }
    $installed = @()
    foreach ($skill in Get-ChildItem $src -Directory) {
        if (-not (Test-Path (Join-Path $skill.FullName 'SKILL.md'))) { continue }
        # Copy rather than link: a junction needs the target to stay put and a symlink needs
        # elevation on Windows. Re-running this script is the refresh.
        Copy-Item $skill.FullName -Destination $dst -Recurse -Force
        $installed += $skill.Name
    }
    Say "skills installed to $dst : $($installed -join ', ')"
}

# ---------------------------------------------------------------- done
Write-Host ""
if ($manualEdit) {
    Write-Host "OK everything except the MCP entry - paste the JSON above into your opencode.jsonc."
} else {
    Write-Host "OK work setup complete."
}
Write-Host "   Restart opencode, then ask it something like 'search the codebase for the retry policy'."
Write-Host "   Tools now available: search_code, get_file, find_usages, get_deps, search_docs, find_convention."
Write-Host ""
Write-Host "   After a reboot, bring the database back with:  .\scripts\start-win.ps1"
Write-Host "   Re-index after big branch changes:             uv run python -m code_context.dev index <repo>"
Write-Host "   Optional semantic notes through your gateway:  see CODE_CONTEXT_OPENAI_* in .env.example"
