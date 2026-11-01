#!/bin/sh
# Claude Code hook. Stdin is the hook payload.
# Always exit 0. A non-zero Stop hook makes Claude Code refuse to stop.
# Never exec: a different taskman binary earlier on PATH must not replace this script.

payload=$(cat)

report() {
  bin=$1
  if ! command -v timeout >/dev/null 2>&1; then
    echo "timeout is not available; activity was not reported" >&2
    return 0
  fi
  if ! timeout 2 "$bin" hook --help >/dev/null 2>&1; then
    echo "taskman at $bin does not provide a hook command; activity was not reported" >&2
    return 0
  fi
  # Shorter than Claude's hook timeout so a stuck client cannot hold the turn.
  printf '%s' "$payload" | timeout 4 "$bin" hook claude >/dev/null 2>&1 || true
  return 0
}

if command -v taskman >/dev/null 2>&1; then
  report "$(command -v taskman)"
  exit 0
fi
if [ -x "${HOME}/.local/bin/taskman" ]; then
  report "${HOME}/.local/bin/taskman"
  exit 0
fi
echo "taskman is not on PATH; activity was not reported" >&2
exit 0
