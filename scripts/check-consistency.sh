#!/usr/bin/env bash
#
# Consistency drift-lint — the MECHANIZABLE half of the change-propagation discipline
# (see CLAUDE.md §Change-propagation map). When one artifact changes, coupled artifacts
# must change with it; this fails CI when they fall out of sync so the drift can't merge
# silently. The non-mechanizable half stays a human checklist in the change-map. Mirrors
# ai-life's scripts/check-consistency.sh.
#
# Design: fast, no build/venv deps — runs on EVERY push/PR including docs-only ones. Each
# check is small, explicit, zero-false-positive. Extend by adding a check block below.
#
# Run locally: bash scripts/check-consistency.sh
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
err() { echo "  ✗ $*" >&2; fail=1; }

# Model-tag families we care about (prefix-match so "qwen3-coder:30b" etc. are caught).
TAG_RE='(qwen|nomic-embed-text|minicpm|bge|llama|deepseek|codellama|starcoder)[A-Za-z0-9._:-]*'

# A model routed to a REMOTE engine instead of Ollama (llm.py CLOUD_PREFIX / OPENAI_PREFIX). Such
# a model is never pulled, so the "must be in the pull list" checks below skip it. Keep the
# prefixes in sync with llm.py — a provider the lint doesn't know would be reported as an
# un-pulled Ollama tag, which is the drift, not the fix.
is_cloud_model() { case "$1" in anthropic:*|openai:*) return 0 ;; *) return 1 ;; esac; }

# ── Check 1: the two pull-models scripts must declare the SAME model set ───────────────
# pull-models.sh and pull-models.ps1 duplicate the list by hand → classic drift: add a
# model to one, forget the other. Compare the extracted tag sets.
echo "check 1: pull-models.sh and pull-models.ps1 declare the same models"
sh_set="$(grep -oE "$TAG_RE" scripts/pull-models.sh | sort -u)"
ps_set="$(grep -oE "$TAG_RE" scripts/pull-models.ps1 | sort -u)"
if [ "$sh_set" != "$ps_set" ]; then
  err "pull-models.sh and pull-models.ps1 disagree on the model set:"
  diff <(printf '%s\n' "$sh_set") <(printf '%s\n' "$ps_set") | sed 's/^/        /' >&2 || true
  err "→ update both scripts so they pull the same models"
fi

# ── Check 2: the embed model actually used must be in the pull list ────────────────────
# config.py default + golden.sh default must be a model pull-models.sh installs, so the
# index can't default to a model nobody pulls.
echo "check 2: the embed model (config.py, golden.sh) is in the pull list"
embed_cfg="$(grep -oE 'embed_model: *str *= *"[^"]+"' src/code_context/config.py | sed 's/.*"\([^"]*\)".*/\1/')"
embed_golden="$(grep -oE 'CODE_CONTEXT_EMBED_MODEL:-[^}"]+' scripts/golden.sh | sed 's/.*:-//')"
for m in $embed_cfg $embed_golden; do
  if ! printf '%s\n' "$sh_set" | grep -qxF -- "$m"; then
    err "embed model '$m' is used but not pulled by scripts/pull-models.sh"
    err "→ add it to pull-models.{sh,ps1} or fix the reference"
  fi
done

# ── Check 3: config.embed_dim must match vector(N) in the schema ───────────────────────
# The "keep in sync" comment on config.embed_dim / the migration's vector(N) — enforce it.
echo "check 3: config.embed_dim == vector(N) in the initial schema"
MIG="src/code_context/migrations/0001_initial_schema.sql"
dim_cfg="$(grep -oE 'embed_dim: *int *= *[0-9]+' src/code_context/config.py | grep -oE '[0-9]+$')"
dim_sql="$(grep -oE 'vector\([0-9]+\)' "$MIG" | grep -oE '[0-9]+' | head -1)"
if [ -n "$dim_cfg" ] && [ -n "$dim_sql" ] && [ "$dim_cfg" != "$dim_sql" ]; then
  err "embed_dim mismatch: config.py=$dim_cfg but $MIG has vector($dim_sql)"
  err "→ set them equal (an embedding model swap changes both)"
fi

# ── Check 4: the notes/analyzer model default must be in the pull list ─────────────────
# config.notes_model (the C-4 enrich analyzer) must be a model pull-models.sh installs, so a
# fresh machine can enrich without a missing-model surprise (same rule as the embed model).
echo "check 4: the notes model (config.py notes_model) is in the pull list"
notes_cfg="$(grep -oE 'notes_model: *str *= *"[^"]+"' src/code_context/config.py | sed 's/.*"\([^"]*\)".*/\1/')"
if [ -n "$notes_cfg" ] && ! is_cloud_model "$notes_cfg" \
   && ! printf '%s\n' "$sh_set" | grep -qxF -- "$notes_cfg"; then
  err "notes model '$notes_cfg' is used but not pulled by scripts/pull-models.sh"
  err "→ add it to pull-models.{sh,ps1} or fix the reference"
fi

# ── Check 5: the rollup model default must be in the pull list ──────────────────────────
# config.rollup_model (the C-4b rollup tier) has a local-capable default; keep it pullable so a
# fresh machine can roll up out of the box. A cloud-tier default (see is_cloud_model) is exempt:
# there is nothing for Ollama to pull, and demanding one would be the drift, not the fix.
echo "check 5: the rollup model (config.py rollup_model) is in the pull list"
rollup_cfg="$(grep -oE 'rollup_model: *str *= *"[^"]+"' src/code_context/config.py | sed 's/.*"\([^"]*\)".*/\1/')"
if [ -n "$rollup_cfg" ] && ! is_cloud_model "$rollup_cfg" \
   && ! printf '%s\n' "$sh_set" | grep -qxF -- "$rollup_cfg"; then
  err "rollup model '$rollup_cfg' is used but not pulled by scripts/pull-models.sh"
  err "→ add it to pull-models.{sh,ps1} or fix the reference"
fi

# ── Check 6: every Settings field must be documented in .env.example ───────────────────
# pydantic's env_prefix makes EVERY Settings field env-settable, so .env.example is the only
# place an operator can discover a knob. A field with no line there is an undiscoverable
# tunable — exactly how notes_timeout_s / module_markers drifted out of the template. A
# commented line (`# KEY=`) counts as documented: the point is discoverability, not a value.
echo "check 6: every config.py Settings field appears in .env.example"
fields="$(sed -n '/^class Settings/,/^settings *=/p' src/code_context/config.py \
  | grep -oE '^    [a-z_]+ *:' | tr -d ' :' | sort -u)"
for f in $fields; do
  key="CODE_CONTEXT_$(printf '%s' "$f" | tr '[:lower:]' '[:upper:]')"
  if ! grep -qE "^#? *${key}=" .env.example; then
    err "config.py field '$f' has no ${key} line in .env.example"
    err "→ add it (commented is fine) so the knob stays discoverable"
  fi
done

# ── Check 7: the ai-life handshake vocabulary must be documented ───────────────────────
# lifecycle.py speaks a contract owned jointly with ANOTHER repo (ai-life LC-4): the endpoint
# path and the two profile names. Neither repo's CI can see the other, so architecture.md is the
# only place this side's half is written down — renaming a profile and leaving the doc behind
# would strand the other end. Backticked form, so the generic word "normal" can't pass by
# accident in prose.
echo "check 7: lifecycle.py's endpoint + profile names appear in architecture.md"
LC="src/code_context/lifecycle.py"
terms="$( { grep -oE '/v1/[a-z-]+' "$LC"; \
            grep -oE '^(CODER_ACTIVE|NORMAL) *= *"[^"]+"' "$LC" | sed 's/.*"\([^"]*\)".*/\1/'; \
          } | sort -u )"
for t in $terms; do
  if ! grep -qF -- "\`$t\`" architecture.md; then
    err "lifecycle contract term '$t' is not documented in architecture.md"
    err "→ add it (backticked) to §Contours, and mirror the change in ../ai-life/plans/lifecycle.md"
  fi
done

# ── Check 8: CODE_CONTEXT_DEFAULT_REPO is documented/written as a NAME, never a path ───
# The indexer stores `Path(repo_path).name` and _repo_clause compares it exactly, so a path in
# this variable matches no row: every tool returns an empty result on a fully populated index,
# which reads as "nothing indexed" rather than as a misconfiguration. work-win.ps1 and the README
# both shipped a path. Path-shaped = contains a slash or a drive letter.
echo "check 8: CODE_CONTEXT_DEFAULT_REPO values are repo names, not paths"
default_repo_vals="$(grep -rhoE "CODE_CONTEXT_DEFAULT_REPO['\"]? *[=:] *['\"][^'\"]+['\"]" \
  README.md scripts/ .env.example 2>/dev/null || true)"
if printf '%s\n' "$default_repo_vals" | grep -qE '[=:] *["'"'"'][^"'"'"']*[/\\]'; then
  err "a CODE_CONTEXT_DEFAULT_REPO example looks like a PATH:"
  printf '%s\n' "$default_repo_vals" | grep -E '[=:] *["'"'"'][^"'"'"']*[/\\]' | sed 's/^/        /' >&2
  err "→ use the indexed directory's leaf name (dev index C:\\src\\my-repo → 'my-repo')"
fi
# The script builds the value rather than quoting one, so assert the leaf-name call is still there.
if grep -q 'CODE_CONTEXT_DEFAULT_REPO' scripts/work-win.ps1 \
   && ! grep -q "CODE_CONTEXT_DEFAULT_REPO'\] = (Split-Path \$Repo -Leaf)" scripts/work-win.ps1; then
  err "work-win.ps1 sets CODE_CONTEXT_DEFAULT_REPO without (Split-Path \$Repo -Leaf)"
  err "→ the MCP entry must carry the repo NAME; a path there empties every tool"
fi

echo ""
if [ "$fail" -ne 0 ]; then
  echo "consistency check FAILED — resolve the ✗ items above." >&2
  exit 1
fi
echo "consistency check passed."
