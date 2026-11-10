# Repository Guidelines

## Product purpose

Taskman is a general-purpose tool and the shared source of truth for development work performed by Codex, Claude Code, or another agent. Store requirements, plans, decisions, progress, and verification evidence in Taskman so another agent can continue from that context. Reservoir is a real-world testing ground; keep project-specific assumptions out of Taskman's core.

Agents execute the lifecycle through local acceptance. The UI creates and edits open work, discards eligible drafts, consolidates completed history, and displays hierarchy, dependencies, progress, and verification. Deployment is outside this workflow.

The CLI supports every operation available in the UI and installs the shared plugin through platform adapters. Open tasks may be refined to reach their agreed outcome. Closed tasks, including requirements and evidence, are immutable; subsequent changes require a new linked task. Journal significant decisions, reasons, and scope refinements during work; summarize them before closing.

## Project Structure & Module Organization

This repository ships the `taskman` marketplace for Codex and Claude Code and its `tm` software-development lifecycle plugin. Marketplace metadata lives in `.claude-plugin/marketplace.json`; plugin manifests, skills, agents, rules, and the legacy task script live under `plugins/tm/`. Product documentation is in `docs/`. The repository's own work items are stored in `tasks/`.

`taskman/` is the Python 3.11+ replacement for `plugins/tm/scripts/taskman.sh`. `src/taskman/commands/` implements Typer commands. Commands use the storage interface in `src/taskman/storage/store.py` and check lifecycle rules through `src/taskman/lifecycle.py`. Two commands are file-format tools and call the file backend directly: `worktree-status` and `migrate`. `src/taskman/storage/file.py` is the file backend. It uses the `src/taskman/model/` modules for names, layout, identifiers, and YAML. `tests/` mirrors those behaviors with pytest; `tests/test_storage_contract.py` holds the cases every backend must pass.

## Build, Test, and Development Commands

Run Python commands from `taskman/`:

```bash
uv sync --extra dev --extra postgres   # create/update the development environment
uv run pytest                   # run the full test suite; Postgres tests skip without a test database
TASKMAN_TEST_DATABASE_URL=postgresql://user:pass@host:5432/taskman_test uv run pytest   # include Postgres; the name must end in _test
uv run pytest tests/test_model_names.py -q
uv run taskman --help           # exercise the local CLI entry point
```

From the repository root, `taskman validate` checks current work-item metadata. Test plugin changes by installing the checkout through `taskman install` and starting fresh sessions in both hosts, as described in `README.md`.

## Coding Style & Naming Conventions

Match the surrounding code; no formatter or linter is configured. Python uses four-space indentation, type annotations, `snake_case` functions/modules, and focused command modules. Keep tests named `test_*.py` with descriptive `test_<behavior>` functions. Plugin skills use `plugins/tm/skills/<verb>/SKILL.md`; work-item slugs use lowercase letters, digits, and underscores.

Edit plugin sources under `plugins/tm/`, not their `.claude/` symlinks. Keep plugin paths self-contained because Claude Code resolves them from the plugin root.

## Testing Guidelines

Add pytest coverage beside every `taskman` behavior change, including success and failure paths. Use `tmp_path` and Typer's `CliRunner` for isolated filesystem and command tests. Run the full suite before submitting. For marketplace or skill changes, also validate manifests and complete the local install/reload smoke test.

## Commit & Pull Request Guidelines

Recent commits use concise, imperative subjects such as `Ship ...`, `Enable ...`, `Remove ...`, and `Update ...`; several append the closed task or feature. Keep each commit scoped to one outcome. Pull requests should state the affected plugin or CLI behavior, link the work item when applicable, list verification performed, and update user-facing documentation when commands or workflows change.

Never edit lifecycle files under `tasks/` directly. Use the Python `taskman` CLI so names, frontmatter, and status transitions stay synchronized. The shell script is for legacy data only; it does not enforce the current lifecycle contract.
