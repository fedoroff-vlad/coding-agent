# macOS dev/runtime environment for coding-agent. Declarative — `brew bundle` installs only
# what's missing (idempotent). Usually run via scripts/bootstrap-mac.sh, or directly:
#   brew bundle --file Brewfile

# --- CLI / build ---
brew "git"
brew "gh"
brew "uv"          # Python toolchain — provisions Python 3.13 itself (see .python-version)
brew "ollama"      # local inference engine (CLI + `brew services` background service)

# --- Apps ---
cask "docker-desktop"          # container runtime (host :5433 dev pgvector, etc.)
cask "claude-code"             # Claude Code CLI (stable channel)
cask "visual-studio-code"
cask "pycharm"                 # Professional (per owner's choice)
cask "datagrip"                # JetBrains DB/SQL IDE (inspect the pgvector index)
cask "postman"

# --- General / workstation apps ---
cask "google-chrome"
cask "yandex"                  # Yandex Browser
cask "telegram"
cask "sublime-text"
brew "mas"                     # Mac App Store CLI — for App-Store-only apps
mas "WireGuard", id: 1451685025 # WireGuard VPN: no brew cask (App Store only); needs App Store sign-in
