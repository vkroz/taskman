#!/bin/sh
# Install the shared CLI, then plugins for explicitly selected hosts.
# curl -fsSL https://raw.githubusercontent.com/vkroz/taskman/main/install.sh | sh -s -- --platform codex --platform claude-code
set -eu
# `taskman install` exits 0 when a requested host CLI is missing. It prints how
# to finish that platform later. A non-zero status means the source was invalid
# or a host CLI was present and its plugin command failed.

if ! command -v uv >/dev/null 2>&1; then
  curl -fsSL https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv tool install --force 'git+https://github.com/vkroz/taskman.git#subdirectory=taskman'
uv tool update-shell
if [ "$#" -gt 0 ]; then
  taskman install "$@"
else
  printf '%s\n' 'CLI installed. Choose plugin hosts: taskman install --platform codex --platform claude-code'
fi
