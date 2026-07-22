#!/usr/bin/env bash
#
# scrub-file.sh — which private terms does THIS file contain? (POSIX twin of scrub-file.ps1;
# keep the two in step, same rule as pull-models.*)
#
#   scripts/scrub-file.sh notes/inbox.md
#
# The pre-commit check (tools/agent-skills/scripts/check-private-terms.*) guards what git can see.
# It deliberately cannot see notes/ — that directory is gitignored precisely so raw capture may name
# real systems. This is the other half: run it on a file you are about to paste into a chat, an
# issue or plans/INBOX.md, and it tells you what to redact first.
#
# Exit: 0 clean, 1 a term was found, 2 misconfigured (no terms file / no such file).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
terms_file="${PRIVATE_TERMS_FILE:-$root/.private-terms}"
path="${1:-}"

if [ -z "$path" ] || [ ! -f "$path" ]; then
  echo "usage: $0 <file>" >&2
  exit 2
fi
if [ ! -f "$terms_file" ]; then
  # Loud, not silent: "no terms" and "no terms file" look identical otherwise, and the second is a
  # false all-clear on the exact file you were about to share.
  echo "scrub-file: no terms file at $terms_file" >&2
  echo "  → create it (one term per line, '#' comments); it must stay gitignored." >&2
  exit 2
fi

mapfile -t terms < <(grep -vE '^\s*(#|$)' "$terms_file" || true)
if [ "${#terms[@]}" -eq 0 ]; then
  echo "scrub-file: $terms_file lists no terms — nothing to check." >&2
  exit 2
fi

found=0
for term in "${terms[@]}"; do
  # -F literal, -i case-insensitive, -n line numbers: the point is to redact, and a term without a
  # location is a search task.
  #
  # LC_ALL=C.UTF-8 is load-bearing (agent-skills#8): Git-Bash inherits an empty locale, and there
  # `grep -i` ABORTS on the first non-ASCII case-fold. Written without it, this script reported
  # "clean" on a file that literally contained the Cyrillic spellings it was asked to find — the
  # same fail-open the upstream hook had, reproduced here within the hour.
  rc=0
  hits="$(LC_ALL=C.UTF-8 grep -Fin -- "$term" "$path")" || rc=$?
  # 1 = no match (the normal path). Anything else is grep failing, and a failed check must never
  # be reported as a clean file.
  if [ "$rc" -gt 1 ]; then
    echo "scrub-file: grep failed (status $rc) on term — refusing to report clean." >&2
    exit 2
  fi
  if [ -n "$hits" ]; then
    [ "$found" -eq 0 ] && echo "✗ private terms in $path:"
    found=1
    printf '%s\n' "$hits" | sed "s/^/   /"
  fi
done

if [ "$found" -eq 1 ]; then
  echo ""
  echo "Rewrite these by SHAPE, not by name — 'a 143-file production Java service', not the"
  echo "repository's name. Keep every number and structural fact; drop only the identity."
  exit 1
fi
echo "scrub-file: $path is clean against ${#terms[@]} terms."
