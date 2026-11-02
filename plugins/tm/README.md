# Taskman plugin

Shared skills for Codex and Claude Code. Install with the Taskman CLI as described in the [project README](../../README.md).

Skill entrypoints are under `skills/`. Claude Code hooks live in `hooks/`. They call `taskman hook claude`, which reports commits, pull requests, and completion for the task in `.taskman/active`. Codex has no plugin hook here; `taskman hooks install` writes a git post-commit hook instead.

Task records live on the Taskman server. The bundled shell script is legacy and does not call that server. Keep paths within this plugin self-contained so installation into either host preserves references.
