#!/usr/bin/env bash
# One-command macOS setup for coding-agent. Idempotent — safe to re-run.
# Installs the toolchain + apps, the Python env, and (by default) the models.
#   ./scripts/bootstrap-mac.sh              # everything
#   SKIP_MODELS=1 ./scripts/bootstrap-mac.sh # tools only (skip the tens-of-GB model pull)
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. Xcode Command Line Tools — git + compilers Homebrew needs.
if ! xcode-select -p >/dev/null 2>&1; then
  echo ">> Installing Xcode Command Line Tools…"
  xcode-select --install || true
  echo "   Finish the CLT installer dialog, then re-run this script."
  exit 1
fi

# 2. Homebrew.
if ! command -v brew >/dev/null 2>&1; then
  echo ">> Installing Homebrew…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# 3. Tools + apps (installs only what's missing).
echo ">> brew bundle…"
brew bundle --file Brewfile || echo ">> some Brewfile entries need attention (e.g. WireGuard = App Store sign-in) — continuing"

# 4. Ollama as a background service.
brew services start ollama >/dev/null 2>&1 || true

# 5. Python env from the lockfile (uv provisions Python 3.13).
echo ">> uv sync…"
uv sync --extra dev --extra index --extra docs

# 6. Models — all of them (running is on demand). Skip with SKIP_MODELS=1.
if [[ "${SKIP_MODELS:-0}" != "1" ]]; then
  echo ">> pulling models (tens of GB; skip with SKIP_MODELS=1)…"
  ./scripts/pull-models.sh
fi

echo ""
echo "✅ coding-agent ready. Launch a dev session:  ./scripts/start-mac.sh"
