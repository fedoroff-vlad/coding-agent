# Pull the Ollama models the coding contour uses. Idempotent - ollama skips models already present.
# Tens of GB total (the 32B coder alone is ~20 GB). Needs Ollama installed and running.
$ErrorActionPreference = 'Stop'

$models = @(
    'qwen3-coder:30b',     # Qwen3-Coder-Flash - the coder, resident during a coding session (~19 GB)
    'nomic-embed-text'     # embeddings for the code-context index
)

foreach ($m in $models) {
    Write-Host ">> ollama pull $m"
    ollama pull $m
}

Write-Host "OK models ready: $($models -join ', ')"
