#!/bin/sh
# Taskman one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/AgentureHQ/taskman/main/install.sh | sh
#
# Installs, in order:
#   1. uv            (provisions Python 3.11+ if absent)
#   2. taskman       (the task CLI the /tm:* skills drive)
#   3. tm plugin    (added to Claude Code via the marketplace)
#
# Re-running upgrades every component. Requires the Claude Code CLI (`claude`)
# to be installed already — see https://claude.com/claude-code.
set -eu

REPO="AgentureHQ/taskman"
MARKETPLACE="taskman"
PLUGIN="tm@taskman"
TASKMAN_SRC="git+https://github.com/${REPO}.git#subdirectory=taskman"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$1" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# 1. uv — installs Python too if needed.
if ! have uv; then
  say "Installing uv (Python package manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv lands in ~/.local/bin; expose it for the rest of this script.
  export PATH="$HOME/.local/bin:$PATH"
  have uv || die "uv installed but not on PATH. Open a new shell and re-run this installer."
fi

# 2. Claude Code CLI — we install a plugin *for* it, so it must already exist.
have claude || die "Claude Code CLI not found. Install it first: https://claude.com/claude-code"

# 3. taskman onto PATH.
say "Installing taskman…"
uv tool install --force "$TASKMAN_SRC"
uv tool update-shell 2>/dev/null || true

# 4. Marketplace + plugin, via the Claude Code CLI.
say "Adding the ${MARKETPLACE} marketplace…"
claude plugin marketplace add "$REPO" 2>/dev/null \
  || claude plugin marketplace update "$MARKETPLACE" 2>/dev/null \
  || warn "Could not add or refresh the marketplace — add it manually with: claude plugin marketplace add $REPO"

say "Installing the ${PLUGIN} plugin…"
claude plugin install "$PLUGIN" --scope user 2>/dev/null \
  || claude plugin update "$PLUGIN" 2>/dev/null \
  || warn "Could not install the plugin — install it manually with: claude plugin install $PLUGIN --scope user"

say "Done."
echo
echo "  Open a NEW terminal (so 'taskman' is on PATH), then start Claude Code:"
echo "      claude"
echo
echo "  Verify with:  taskman version"
