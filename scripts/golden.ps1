# Run the golden lanes (retrieval + notes + rollups + docs) against a REAL Ollama + pgvector. Opt-in (not in unit CI).
# Brings up the dev DB, ensures the embed + notes + rollup models, migrates, then runs the `golden` marker.
# NOTE: golden works on a clean slate - it leaves the dev DB holding only the fixture (re-index after).
# Extra args are forwarded to pytest for a subset (e.g. .\scripts\golden.ps1 tests\test_golden_retrieval.py
# or .\scripts\golden.ps1 -k rollup). No args = all golden-marked tests.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

docker compose -f infra/docker-compose.yml up -d
uv sync --extra dev --extra index --extra docs
uv run python -m code_context.dev migrate
$model = if ($env:CODE_CONTEXT_EMBED_MODEL) { $env:CODE_CONTEXT_EMBED_MODEL } else { 'nomic-embed-text' }
ollama pull $model | Out-Null
$notes = if ($env:CODE_CONTEXT_NOTES_MODEL) { $env:CODE_CONTEXT_NOTES_MODEL } else { 'qwen3:8b' }
ollama pull $notes | Out-Null   # the notes lane drives a real analyzer
$rollup = if ($env:CODE_CONTEXT_ROLLUP_MODEL) { $env:CODE_CONTEXT_ROLLUP_MODEL } else { 'qwen3:8b' }
ollama pull $rollup | Out-Null  # the rollup lane drives the rollup tier

uv run pytest -m golden -v @args
