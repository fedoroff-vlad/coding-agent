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

# The one thing this script cannot guess and cannot work without. Ask for it rather than
# "succeeding" into an empty index - the whole setup then looks done and answers nothing. Only
# when a human is there to answer: a non-interactive host (CI, a pipe) keeps the old warn-and-skip.
if (-not $Repo -and $Host.UI.RawUI -and -not [Console]::IsInputRedirected) {
    Write-Host ""
    Write-Host "Which repository should this setup point at? A local working copy, e.g. C:\src\my-monorepo."
    Write-Host "It gets indexed (unless -SkipIndex) and becomes opencode's default retrieval scope."
    $answer = Read-Host "   path (Enter to skip)"
    if ($answer) { $Repo = $answer.Trim('"') }   # a pasted Windows path often arrives quoted
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

# ---------------------------------------------------------------- 0. a fresh clone
# `git clone` without --recurse-submodules leaves tools/agent-skills an empty directory. The skills
# step at the end would then just print a hint and finish "OK" - so fix it here instead, where the
# fix is one command and the machine is still being set up.
if (-not (Get-ChildItem (Join-Path $root 'tools\agent-skills') -ErrorAction SilentlyContinue)) {
    Say 'tools/agent-skills is empty (clone without --recurse-submodules) - initialising...'
    # git reports progress on STDERR, and under ErrorActionPreference='Stop' PowerShell 5.1 turns a
    # native command's stderr into a terminating NativeCommandError - so a perfectly successful
    # `submodule update` aborts the script. Capture the streams and judge by the exit code, the
    # same way start-win.ps1 handles `docker info`.
    $out = git submodule update --init --recursive 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Host $out
        throw "git submodule update failed (exit $LASTEXITCODE) - check network/credentials for the agent-skills remote."
    }
}

# The private-terms guard does NOT travel with the clone: the terms file is gitignored (a published
# denylist is the leak, indexed) and the hook lives in .git/hooks. So every clone starts unprotected,
# and this one is about to be pointed at an employer's source. Install the hook; the terms are the
# operator's to supply, and out of band - never through the repo.
$hook = Join-Path $root '.git\hooks\pre-commit'
if ((Test-Path (Join-Path $root '.git')) -and -not (Test-Path $hook)) {
    # LF and no BOM, written by hand: Set-Content would end the shebang line with CRLF, and a
    # `#!/bin/sh<CR>` is the classic "bad interpreter" - Git for Windows tolerates it, the same
    # clone on the Mac does not.
    [System.IO.File]::WriteAllText($hook,
        "#!/bin/sh`nexec `"`$(git rev-parse --show-toplevel)`"/tools/agent-skills/scripts/check-private-terms.sh`n",
        (New-Object System.Text.UTF8Encoding $false))
    Say 'installed the private-terms pre-commit hook (.git/hooks/pre-commit)'
}
$termsFile = Join-Path $root '.private-terms'
if (-not (Test-Path $termsFile)) {
    # Write the file, don't just ask for it. "Create a denylist" is a blank page and a judgement
    # call about what counts as identifying; a template with the CATEGORIES filled in turns it into
    # a two-minute fill-in. Every line is a comment, so the checker still reports "lists no terms"
    # and keeps refusing commits until a real term is added - a template must not read as done.
    [System.IO.File]::WriteAllText($termsFile, (@(
        '# .private-terms - local denylist for check-private-terms (the scrub-identity skill).',
        '#',
        '# NEVER COMMIT THIS FILE. It is gitignored, and that is the point: a published list of',
        '# what you are hiding is the leak itself, with an index attached. Carry it between',
        '# machines out of band - not through this repo, not through anything with a history.',
        '#',
        '# One literal term per line, case-insensitive substring match. Uncomment and fill in;',
        '# lines starting with # are ignored, so the file below currently protects NOTHING.',
        '',
        '# --- The employer, in EVERY spelling you might type ---',
        '# Both alphabets, the legal form, the abbreviation, the way it appears in a package name.',
        '# <company name>',
        '# <Company Name in Latin>',
        '# <ABBR>',
        '',
        '# --- Internal domains and hostnames ---',
        '# The LLM gateway, the wiki, the registry, anything .local / .corp / .internal.',
        '# <company>.example.internal',
        '',
        '# --- Internal system and service names ---',
        '# The ones that appear in package paths and class names - these leak through a quoted',
        '# stack trace or an example far more often than the company name does.',
        '# <internal-service-name>',
        '',
        '# --- Confluence space keys, repository names, team names ---',
        '# <SPACEKEY>',
        '',
        '# --- Domain vocabulary that identifies the industry ---',
        '# A "synthetic" fixture written in the vocabulary of one industry names its owner almost',
        '# as precisely as a logo would.',
        '# <domain term>',
        ''
    ) -join "`n"), (New-Object System.Text.UTF8Encoding $false))
    Say "wrote a .private-terms template - FILL IT IN (it is gitignored and protects nothing yet)"
}
if (-not (Get-Content $termsFile | Where-Object { $_ -notmatch '^\s*(#|$)' })) {
    Write-Host ""
    Write-Host "!! .private-terms has no terms yet - this repo is PUBLIC and nothing is guarding it."
    Write-Host "   Open it and uncomment/fill the categories: $termsFile"
    Write-Host "   Until then every commit here is refused by the hook, which is the intended state."
    Write-Host "   Background: tools/agent-skills/skills/scrub-identity/SKILL.md"
}

if (-not $WireOnly) {
    # ------------------------------------------------------------ 1. infra + python env
    # Two prerequisites this script installs neither of: Docker Desktop (a reboot and, on a managed
    # box, an administrator) and Ollama. start-win.ps1 does reach them, but reports the miss as
    # "Docker isn't running" - which on a fresh machine sends you looking for a service that was
    # never installed. Name them here, with the exact command, before anything else runs.
    $missing = @()
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { $missing += 'Docker.DockerDesktop' }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { $missing += 'Ollama.Ollama' }
    if ($missing) {
        Write-Host ""
        Write-Host "!! Missing prerequisite(s): $($missing -join ', ')"
        Write-Host "   Install them, then re-run this script:"
        foreach ($m in $missing) { Write-Host "     winget install --id $m" }
        Write-Host "   (Docker Desktop needs a reboot, and an administrator on a managed machine.)"
        Write-Host "   Everything else - uv, the Python env, the models - this script handles."
        throw "prerequisites missing: $($missing -join ', ')"
    }

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
    # Step 0 already tried `git submodule update --init`; still empty means that failed (no network,
    # no credentials for the skills remote), so say so instead of repeating the same advice.
    Write-Host "!! $src is still empty after a submodule init - check network/credentials, then re-run with -WireOnly."
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
