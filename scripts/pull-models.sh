#!/usr/bin/env bash
# Pull the Ollama models the coding contour uses. Idempotent — ollama skips models already present.
# Tens of GB total (the 32B coder alone is ~20 GB). Needs the ollama service running.
set -euo pipefail

MODELS=(
  "qwen3-coder:30b"     # Qwen3-Coder-Flash — the coder, resident during a coding session (~19 GB)
  "nomic-embed-text"    # embeddings for the code-context index
)

for m in "${MODELS[@]}"; do
  echo ">> ollama pull $m"
  ollama pull "$m"
done

echo "✅ models ready: ${MODELS[*]}"
