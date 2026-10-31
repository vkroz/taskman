#!/bin/sh
# Claude Code hook. Stdin is the hook payload. Exit 0 even when taskman is missing
# so an agent turn is not blocked.
if command -v taskman >/dev/null 2>&1; then
  exec taskman hook claude
fi
if [ -x "${HOME}/.local/bin/taskman" ]; then
  exec "${HOME}/.local/bin/taskman" hook claude
fi
echo "taskman is not on PATH; activity was not reported" >&2
exit 0
