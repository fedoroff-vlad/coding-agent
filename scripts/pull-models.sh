#!/usr/bin/env bash
# Pull the Ollama models the coding contour uses. Idempotent — ollama skips models already present.
# Tens of GB total (the 32B coder alone is ~20 GB). Needs the ollama service running.
#   ./scripts/pull-models.sh                    # full set (coder + embeddings)
#   ./scripts/pull-models.sh --embeddings-only  # index-only boxes (e.g. the CPU-only Windows VDI) — skip the coder
set -euo pipefail

MODELS=(
  "qwen3-coder:30b"     # Qwen3-Coder-Flash — the coder, resident during a coding session (~19 GB)
  "nomic-embed-text"    # embeddings for the code-context index
)
# Machines that only index/search (no local inference) skip the ~19 GB coder — the coder runs on the Mac.
if [[ "${1:-}" == "--embeddings-only" ]]; then
  MODELS=( "nomic-embed-text" )
fi

for m in "${MODELS[@]}"; do
  echo ">> ollama pull $m"
  ollama pull "$m"
done

echo "✅ models ready: ${MODELS[*]}"
