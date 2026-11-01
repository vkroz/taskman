#!/bin/sh
# Claude Code hook. Stdin is the hook payload.
# Always exit 0. A non-zero Stop hook makes Claude Code refuse to stop.
# Never exec: a different taskman binary earlier on PATH must not replace this script.

payload=$(cat)

report() {
  bin=$1
  # Stop the client after 4 s, which is shorter than Claude's 5 s hook timeout.
  # macOS has no `timeout`; Homebrew coreutils installs it as `gtimeout`.
  # With neither, run the client unwrapped: its own 3 s network timeout applies.
  if command -v timeout >/dev/null 2>&1; then
    limit="timeout 4"
  elif command -v gtimeout >/dev/null 2>&1; then
    limit="gtimeout 4"
  else
    limit=""
  fi
  # $limit is unquoted on purpose so that an empty value expands to nothing.
  # Every event from this hook records the author as claude-code.
  printf '%s' "$payload" | $limit "$bin" hook claude --agent claude-code >/dev/null 2>&1
  status=$?
  if [ "$status" -eq 2 ]; then
    echo "taskman at $bin does not support 'hook claude --agent'; update taskman. Activity was not reported" >&2
  elif [ "$status" -eq 124 ]; then
    echo "taskman hook timed out; activity was not reported" >&2
  fi
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
