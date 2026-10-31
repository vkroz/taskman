# Taskman

@AGENTS.md

Use the Python `taskman` CLI against the Taskman server. Before working an existing item, run `taskman pull ID` and read `.taskman/tasks/ID.md`. Read `taskman help` for the storage contract and `taskman context ID` for shared state. Do not edit files under `tasks/` for live work; those files are historical and are not imported. Edit plugin sources under `plugins/tm/`, not installed caches or `.claude/` symlinks.
