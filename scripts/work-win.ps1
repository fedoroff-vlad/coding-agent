# One-command setup of coding-agent on a WORK machine (Windows), where the LLM is a company
# gateway and the shell is opencode. Idempotent - safe to re-run (re-running is also how you
# refresh the installed skills after a submodule bump).
#
#   .\scripts\work-win.ps1 -Repo C:\path\to\your\monorepo
#   .\scripts\work-win.ps1 -Repo C:\path\to\repo -SkipIndex   # everything but the (slow) indexing
#   .\scripts\work-win.ps1 -WireOnly                          # just opencode: re-register + refresh skills
#
# What it does: dev session (Docker + uv + pgvector + migrations) -> the embed model -> index your
# repo -> install opencode -> point it at your company gateway -> register the code-context MCP
# server in it -> install the dev-workflow skills.
#
# Retrieval needs NO analyzer model, so the infrastructure half never asks for the gateway: after
# it finishes you can already search and read code. The gateway is what the SHELL thinks with, and
# it is a separate channel from the optional `enrich`/`rollup` analyzer (CODE_CONTEXT_OPENAI_* in
# .env.example) - the same URL and key, read by two different consumers.
#
#   .\scripts\work-win.ps1 -Repo C:\repo -GatewayUrl https://<gateway>/v1 -Model <model-id>
#
# The API KEY IS NEVER A PARAMETER: a secret on a command line lands in PSReadLine history and in
# the process list. The config gets opencode's `{env:...}` reference and the value stays in your
# environment - the same rule llm.py follows for the analyzer tier.
#
# Windows-only on purpose: the work machine is Windows. The portable form of every step is the
# manual sequence in README "Use it on a work machine" - keep the two in step.
param(
    [string]$Repo,
    [switch]$SkipIndex,
    # Skip the infrastructure half entirely - no Docker, no model pull, no indexing. For
    # re-registering the MCP server or refreshing the skills after a submodule bump.
    [switch]$WireOnly,
    [string]$DbDsn = 'postgresql://dev:dev@localhost:5433/code_context',
    # The company LLM gateway, OpenAI dialect - INCLUDING the /v1 suffix, same value as
    # CODE_CONTEXT_OPENAI_BASE_URL. Omit to leave opencode's provider config alone.
    [string]$GatewayUrl,
    # A model id the gateway exposes; becomes opencode's default model.
    [string]$Model,
    # Provider id in opencode's config (and the prefix of the model string). Deliberately generic:
    # this repo is public, and an employer's name does not belong in a committed default.
    [string]$ProviderId = 'work-gateway'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Say($msg) { Write-Host ">> $msg" }

function Sync-PathFromMachine {
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path', 'User')
}

if ($Repo) {
    if (-not (Test-Path $Repo)) { throw "-Repo '$Repo' does not exist." }
    $Repo = (Resolve-Path $Repo).Path   # opencode gets an absolute path, whatever you typed
}
# A gateway without a model would write a provider nothing selects, and a model without a gateway
# has nowhere to run. Fail here rather than half-write the config.
if ($GatewayUrl -and -not $Model) { throw "-GatewayUrl needs -Model (the model id the gateway exposes)." }
if ($Model -and -not $GatewayUrl) { throw "-Model needs -GatewayUrl (the gateway's /v1 base URL)." }
if ($GatewayUrl -and $GatewayUrl -notmatch '/v\d+/?$') {
    # The OpenAI dialect is versioned and the SDK appends only the path after it: a base URL
    # without /v1 fails at the first call with a 404, far from the cause.
    Write-Host "!! -GatewayUrl '$GatewayUrl' does not end in /v1 - the OpenAI dialect wants the version prefix."
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

# ---------------------------------------------------------------- 4. opencode itself
# Writing a config for a shell that isn't installed is the failure mode this check exists for:
# every step below "succeeds" and nothing runs. winget id from `winget search opencode`.
if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    # A shell opened before the install has a stale PATH copy, and re-running winget for an
    # already-installed package is a slow no-op with a confusing message. Re-read PATH first.
    Sync-PathFromMachine
}
if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    Say 'opencode not found - installing (winget SST.opencode)...'
    winget install --id SST.opencode --accept-source-agreements --accept-package-agreements --silent
    Sync-PathFromMachine   # winget edits the persisted PATH, not this shell's copy
    if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
        Write-Host "!! opencode still not on PATH - open a new shell to pick it up (the config below is written regardless)."
    }
} else {
    Say "opencode present: $((Get-Command opencode).Source)"
}

# ---------------------------------------------------------------- 5. opencode: gateway + MCP server
$cfgDir = if ($env:XDG_CONFIG_HOME) { Join-Path $env:XDG_CONFIG_HOME 'opencode' }
          else { Join-Path $env:USERPROFILE '.config\opencode' }
$jsonPath  = Join-Path $cfgDir 'opencode.json'
$jsoncPath = Join-Path $cfgDir 'opencode.jsonc'

$envBlock = [ordered]@{ CODE_CONTEXT_DB_DSN = $DbDsn }
# The repo IDENTIFIER, not the path: the indexer stores `Path(repo_path).name` and the scope
# filter is an exact string compare, so a path here matches no row and every tool silently
# returns nothing - the worst failure shape, since the setup looks complete.
if ($Repo) { $envBlock['CODE_CONTEXT_DEFAULT_REPO'] = (Split-Path $Repo -Leaf) }
$entry = [pscustomobject]@{
    type        = 'local'
    command     = @('uv', 'run', '--directory', $root, 'code-context')
    enabled     = $true
    environment = [pscustomobject]$envBlock
}

# The gateway the SHELL thinks with. `@ai-sdk/openai-compatible` is opencode's driver for a plain
# /v1/chat/completions endpoint - the same dialect llm.py's `openai:` tier speaks, so one gateway
# serves both. The key is written as opencode's `{env:...}` reference, never as its value: this
# file is world-readable on the box and lands in backups.
$keyVar = 'CODE_CONTEXT_OPENAI_API_KEY'
if ($GatewayUrl) {
    $providerEntry = [pscustomobject]@{
        npm     = '@ai-sdk/openai-compatible'
        name    = 'Work LLM gateway'
        options = [pscustomobject]@{ baseURL = $GatewayUrl; apiKey = "{env:$keyVar}" }
        models  = [pscustomobject]@{ $Model = [pscustomobject]@{ name = $Model } }
    }
}

# Set-or-replace on a PSCustomObject: ConvertFrom-Json gives note properties, and Add-Member throws
# on one that already exists - which is exactly the re-run case this script promises to survive.
function Set-Prop($obj, [string]$name, $value) {
    if ($obj.PSObject.Properties[$name]) { $obj.$name = $value }
    else { $obj | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}

if (Test-Path $jsoncPath) {
    # A .jsonc is hand-written and may carry comments; rewriting it through ConvertTo-Json would
    # silently delete them. Print the snippet instead - your file, your edit.
    Write-Host ""
    Write-Host "!! Found $jsoncPath (comments would not survive an automatic rewrite)."
    Write-Host "   Add this entry under its top-level 'mcp' key yourself:"
    Write-Host (@{ 'code-context' = $entry } | ConvertTo-Json -Depth 10)
    if ($GatewayUrl) {
        # Backtick, not backslash: PowerShell's escape character inside a double-quoted string.
        Write-Host "   ...and this one under 'provider', plus a top-level `"model`": `"$ProviderId/$Model`""
        Write-Host (@{ $ProviderId = $providerEntry } | ConvertTo-Json -Depth 10)
    }
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
    Set-Prop $cfg.mcp 'code-context' $entry        # idempotent: re-run updates the paths

    if ($GatewayUrl) {
        if (-not $cfg.PSObject.Properties['provider']) {
            $cfg | Add-Member -NotePropertyName 'provider' -NotePropertyValue ([pscustomobject]@{})
        }
        Set-Prop $cfg.provider $ProviderId $providerEntry
        # Only this provider's own default. Another provider you already configured keeps its
        # models; what changes is which one a new session starts on.
        Set-Prop $cfg 'model' "$ProviderId/$Model"
        Say "provider '$ProviderId' -> $GatewayUrl (model $Model)"
    }

    # .NET writer, not Set-Content: PowerShell 5.1 writes UTF-8 *with* a BOM, and a BOM in front
    # of a JSON document trips strict parsers.
    [System.IO.File]::WriteAllText($jsonPath, ($cfg | ConvertTo-Json -Depth 10))
}

# The key never touches this script, but a missing one fails at the first prompt with an opaque
# 401 - so check for it here, where the cause is still on screen.
if ($GatewayUrl -and -not (Get-Item "env:$keyVar" -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "!! $keyVar is not set - opencode will resolve {env:$keyVar} to nothing and the gateway will 401."
    Write-Host "   Set it for future shells (user scope, not the repo):"
    Write-Host "     setx $keyVar `"<your-key>`""
    Write-Host "   ...and for THIS shell:  `$env:$keyVar = '<your-key>'"
    Write-Host "   The same variable is what the optional analyzer tier reads - one gateway, one key."
}

# ---------------------------------------------------------------- 6. opencode: the skills
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
if (-not $GatewayUrl) {
    Write-Host ""
    Write-Host "!! No -GatewayUrl given: opencode's own model is NOT configured by this run."
    Write-Host "   The MCP tools are wired, but the shell has nothing to think with until you either"
    Write-Host "   re-run with -GatewayUrl https://<gateway>/v1 -Model <model-id>, or configure a"
    Write-Host "   provider yourself (opencode: /models)."
}
Write-Host ""
Write-Host "   After a reboot, bring the database back with:  .\scripts\start-win.ps1"
Write-Host "   Re-index after big branch changes:             uv run python -m code_context.dev index <repo>"
Write-Host "   Optional semantic notes through your gateway:  see CODE_CONTEXT_OPENAI_* in .env.example"

# Reaching here means every step ran (a real failure throws under ErrorActionPreference='Stop').
# Without this the script inherits $LASTEXITCODE from the last native call - winget returns
# non-zero for "already installed", which would read as a failed setup to anything scripting this.
exit 0
