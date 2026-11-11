# Taskman plugin

Shared skills for Codex and Claude Code. Install with the Taskman CLI as described in the [project README](../../README.md).

Skill entrypoints are under `skills/`. Task persistence belongs to the Python `taskman` CLI, which stores work items in SQLite or Postgres. Keep paths within this plugin self-contained so installation into either host preserves references.
