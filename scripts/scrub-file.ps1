# scrub-file.ps1 — which private terms does THIS file contain?
#
#   .\scripts\scrub-file.ps1 notes\inbox.md
#
# The pre-commit check (tools/agent-skills/scripts/check-private-terms.*) guards what git can see.
# It deliberately cannot see notes/ — that directory is gitignored precisely so raw capture may name
# real systems. This is the other half: run it on a file you are about to paste into a chat, an
# issue or plans/INBOX.md, and it tells you what to redact first.
#
# Exit: 0 clean, 1 a term was found, 2 misconfigured (no terms file / no such file).
param([Parameter(Mandatory = $true)][string]$Path)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$termsFile = if ($env:PRIVATE_TERMS_FILE) { $env:PRIVATE_TERMS_FILE } else { Join-Path $root '.private-terms' }

if (-not (Test-Path $Path)) { Write-Host "scrub-file: no such file: $Path"; exit 2 }
if (-not (Test-Path $termsFile)) {
    # Loud, not silent: "no terms" and "no terms file" look identical in the output otherwise, and
    # the second one is a false all-clear on the exact file you were about to share.
    Write-Host "scrub-file: no terms file at $termsFile"
    Write-Host "  -> run .\scripts\work-win.ps1 -WireOnly to get a template, then fill it in."
    exit 2
}

# -Encoding UTF8 on BOTH reads, and it is load-bearing: PowerShell 5.1's Get-Content defaults to the
# system ANSI codepage, so a BOM-less UTF-8 terms file comes back as mojibake and every non-ASCII
# term silently matches nothing. A denylist that quietly skips half its entries is worse than no
# denylist, because it reports "clean". Found exactly this way — the Cyrillic spellings of a company
# name were invisible while the Latin ones were caught.
$terms = Get-Content $termsFile -Encoding UTF8 |
    Where-Object { $_ -notmatch '^\s*(#|$)' } | ForEach-Object { $_.Trim() }
if (-not $terms) { Write-Host "scrub-file: $termsFile lists no terms - nothing to check."; exit 2 }

# Line-numbered hits: the point is to redact them, and a term without a location is a search task.
$lines = Get-Content $Path -Encoding UTF8
$found = $false
foreach ($term in $terms) {
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -like "*$term*") {          # -like is literal + case-insensitive, no regex
            if (-not $found) { Write-Host "x private terms in ${Path}:"; $found = $true }
            Write-Host ("   line {0,4}: {1}" -f ($i + 1), $term)
        }
    }
}

if ($found) {
    Write-Host ""
    Write-Host "Rewrite these by SHAPE, not by name - 'a 143-file production Java service', not the"
    Write-Host "repository's name. Keep every number and structural fact; drop only the identity."
    exit 1
}
Write-Host "scrub-file: $Path is clean against $(($terms | Measure-Object).Count) terms."
exit 0
