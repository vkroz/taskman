# Repository Guidelines

## Product purpose

Taskman is a general-purpose tool and the shared source of truth for development work performed by Codex, Claude Code, or another agent. Store requirements, plans, decisions, progress, and verification evidence in the Taskman server so another agent can continue from that context. Reservoir is a real-world testing ground; keep project-specific assumptions out of Taskman's core.

The server keeps tasks in Postgres. Agents use the `taskman` CLI over HTTP. Before editing a repository for an existing item, run `taskman pull ID --agent <author>` (the author of your changes, for example `codex`) and read the working copy under `.taskman/tasks/`. The server does not write the checkout. Hooks report commits, pull requests, and completion. The web UI is a flight-control view of those reports. Deployment of the product under work is outside this workflow.

## Project Structure & Module Organization

This repository ships the `taskman` marketplace for Codex and Claude Code and its `tm` software-development lifecycle plugin. Marketplace metadata lives in `.claude-plugin/marketplace.json`; plugin manifests, skills, agents, rules, hooks, and the legacy task script live under `plugins/tm/`. Product documentation is in `docs/`. Files under `tasks/` are historical text from the file-backed edition. They are not imported into Postgres.

`taskman/` is the Python 3.11+ server and HTTP CLI. `src/taskman/db.py` and `store.py` talk to Postgres; `httpapp.py` is the WSGI app; `commands/` implements the CLI. `tests/` covers the API, storage, hooks, and installer.

## Build, Test, and Development Commands

Run Python commands from `taskman/`:

```bash
uv sync --extra dev --extra serve   # create/update the development environment
uv run pytest                        # full suite; starts Postgres via Docker unless TASKMAN_TEST_DATABASE_URL is set
uv run taskman --help                # exercise the local CLI entry point
```

From the repository root, `docker compose up -d --build` runs the server and Postgres. `taskman validate` checks that the server is reachable and the token works. Test plugin changes by installing the checkout through `taskman install` and starting a fresh host session, as described in `README.md`.

## Coding Style & Naming Conventions

Match the surrounding code; no formatter or linter is configured. Python uses four-space indentation, type annotations, `snake_case` functions/modules, and focused command modules. Keep tests named `test_*.py` with descriptive `test_<behavior>` functions. Plugin skills use `plugins/tm/skills/<verb>/SKILL.md`; work-item slugs use lowercase letters, digits, and underscores.

Edit plugin sources under `plugins/tm/`, not their `.claude/` symlinks. Keep plugin paths self-contained because Claude Code resolves them from the plugin root.

## Testing Guidelines

Add pytest coverage beside every `taskman` behavior change, including success and failure paths. Use `tmp_path` and Typer's `CliRunner` for isolated filesystem and command tests. Run the full suite before submitting. For marketplace or skill changes, also validate manifests and complete the local install/reload smoke test.

## Commit & Pull Request Guidelines

Recent commits use concise, imperative subjects such as `Ship ...`, `Enable ...`, `Remove ...`, and `Update ...`; several append the closed task or feature. Keep each commit scoped to one outcome. Pull requests should state the affected plugin or CLI behavior, link the work item when applicable, list verification performed, and update user-facing documentation when commands or workflows change.

Do not treat files under `tasks/` as the live store. Create and update work with the Python `taskman` CLI against the server. Copy any historical file text into `taskman new` by hand. The shell script under `plugins/tm/scripts/` is legacy and does not talk to the server.
