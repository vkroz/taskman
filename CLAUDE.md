# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Foundational rules to always follow

@.claude/rules/first-principles.md

## Architecture rework complete

Epic `agentic_sdlc_rework` has shipped all six features:

- `unified_skills_and_cleanup` — verb-noun skill surface `/tm:define|design|plan|implement|validate <level>` is live.
- `rules_split_and_new_files` — composition rules in `rules/task-composition.md`; persistence in `taskman.sh help`; role-specific rule files `rules/qa.md` and `rules/doc-maintenance.md`.
- `planner_subagent` — Planner sub-agent at `plugins/tm/agents/planner.md`; `/tm:define`, `/tm:design`, `/tm:plan` delegate composition to it.
- `task_escalation_protocol` — `/tm:implement task` detects design gaps, writes a gap-log to `tasks/gaps/`, halts with a routing message, supports resume.
- `qa_subagent_and_validation` — QA sub-agent at `plugins/tm/agents/qa.md`; `/tm:validate feature|epic|product` delegate to it; `/tm:validate task` runs lightweight gates in the main session.
- `docsync_close_hook` — `taskman.sh` close actions append to `tasks/docs-sync-queue.txt`; `/tm:docs-sync` processes the queue, reviews upstream docs for drift per `rules/doc-maintenance.md`, proposes diffs.

## What this repo is

A Claude Code **plugin marketplace** (`taskman`) that ships one plugin today: `tm` — an agentic SDLC loop. There is no application code, no build, and no test framework. The deliverables are:

- `.claude-plugin/marketplace.json` — marketplace manifest
- `plugins/<name>/` — self-contained plugin directories (skills, rules, scripts, optional agents/commands/hooks/.mcp.json)
- `docs/` — product docs for the marketplace itself
- `tasks/` — this repo's own SDLC tracking, dogfooded through `tm`

## Dogfooding pattern (important)

The repo runs its own plugin via symlinks under `.claude/`:

```
.claude/skills -> ../plugins/tm/skills
.claude/rules  -> ../plugins/tm/rules
```

Consequences:
- `/tm:*` skills are loaded automatically when Claude Code opens this repo — no `/plugin install` needed during local development.
- The rule files (`rules/first-principles.md`, `rules/task-composition.md`, `rules/writing-guideline.md`, `rules/qa.md`, `rules/doc-maintenance.md`) live at `plugins/tm/rules/` and are exposed at `.claude/rules/`. Edit them in `plugins/tm/rules/`; the `.claude/rules` path is a symlink. Persistence rules (storage, naming, lifecycle) live in `./plugins/tm/scripts/taskman.sh help`, not in any rule file.
- When editing a skill, edit `plugins/tm/skills/<skill>/SKILL.md`, not the symlinked copy.

## taskman.sh is the only writer for task state

All epic / feature / task mutations go through `plugins/tm/scripts/taskman.sh`. Skills compose content in dialog with the user, then hand off to taskman as the save step. Never write epic, feature, or task files under `tasks/{epics,features,backlog,active,done}/` directly — invariants (folder ↔ `status` YAML field, draft markers, lifecycle preconditions) are enforced by the script.

**Exceptions** — observability records, not lifecycle units, written outside `taskman.sh`:

- `tasks/gaps/` — gap-log files produced by `/tm:implement task` when a design gap halts coding. Format documented in `plugins/tm/skills/implement/SKILL.md` under "Design gap escalation protocol".
- `tasks/docs-sync-queue.txt` — PostClose hook queue, appended by `taskman.sh` on each close action; cleared by `/tm:docs-sync` after upstream docs are reviewed. Format documented in `taskman.sh help` under "DOC-SYNC QUEUE".

From the repo root, invoke it as `./plugins/tm/scripts/taskman.sh ...`. When working inside the plugin, `./scripts/taskman.sh` is equivalent. The script resolves `TASKS_DIR` relative to itself, so the active task tree is `plugins/tm/tasks/` *if invoked from inside the plugin* — but this repo's tracked tasks live in the **top-level** `tasks/` folder. Set `TASKMAN_TASKS_DIR=$PWD/tasks` when running from the root if the tasks folder being mutated isn't the one you expect.

Common commands:

```bash
./plugins/tm/scripts/taskman.sh help
./plugins/tm/scripts/taskman.sh validate
./plugins/tm/scripts/taskman.sh list tasks   [--status backlog|active|done] [--kind task|bug]
./plugins/tm/scripts/taskman.sh move <task-path> <backlog|active|done>
./plugins/tm/scripts/taskman.sh feature close <slug>   # fails unless all member tasks are done
./plugins/tm/scripts/taskman.sh epic close    <slug>   # fails unless all member features are done
```

`taskman.sh new ...` writes files with `draft: true` in the YAML header for preview, then `finalize <path>` clears the marker or `discard <path>` deletes the draft (refuses non-drafts).

## Adding a new plugin to the marketplace

1. Create `plugins/<your-plugin>/` containing its own `.claude-plugin/plugin.json`. All referenced paths (skills, agents, commands, hooks, MCP) must be self-contained inside that plugin directory — Claude Code resolves them relative to the plugin root.
2. Add a new entry to the `plugins` array in `.claude-plugin/marketplace.json`.
3. Update the **Available plugins** table in the top-level `README.md`.

To test the marketplace locally before publishing:

```
/plugin marketplace add ./
/plugin install <plugin-name>@taskman
```

## Work-unit hierarchy (when using /tm:* skills)

```
product → epic → feature → task
```

Each tier is optional one level up. A task may have no feature; a feature may have no epic. The hierarchy is open, not enforced — `taskman.sh validate` downgrades legacy / stale references to warnings.

File locations:
- Product docs: `docs/vision.md`, `docs/spec.md`, `docs/requirements.md`, `docs/architecture.md`
- Epics: `tasks/epics/YYYYMMDD_<slug>.md` (flat, no lifecycle subfolders; `status` field tracks state)
- Features: `tasks/features/YYYYMMDD_<slug>.md` (same shape as epics)
- Tasks: `tasks/{backlog,active,done}/YYYYMMDD[_NN]_<slug>.md` (folder is source of truth, `status` YAML must agree)

Slug format: `[a-z][a-z0-9_]*` — lowercase, underscores only, no hyphens or dots.

See `plugins/tm/rules/task-composition.md` for frontmatter shapes, body sections, and the mandatory `## Summary` template. See `./plugins/tm/scripts/taskman.sh help` for storage layout, naming, lifecycle preconditions, and validation behavior.

## Editing skills

A skill lives at `plugins/tm/skills/<name>/SKILL.md`. The frontmatter `name:` field becomes the `/tm:<name>` invocation. The body is the prompt that runs when the skill is invoked. Skills should never write task files directly — they call `taskman.sh` for any persistence.

## Editing agents

Sub-agents live at `plugins/tm/agents/<name>.md`. The frontmatter `name:` field becomes the `subagent_type` used when a skill invokes them via the Agent tool. Two agents ship today:

- `planner` — level-aware Design + Plan composer used by `/tm:define`, `/tm:design`, `/tm:plan`. Read-only tools (no Write/Edit/Bash); returns text for the parent to persist via `taskman.sh`.
- `qa` — fresh-context validator used by `/tm:validate feature|epic|product`. Full tools (Read/Bash/Write/Edit) so it can run tests, apply in-scope glue fixes, and write reports; the role boundary lives in the system prompt + `rules/qa.md`.

The pattern: sub-agents isolate by context (they don't see the parent conversation), not by tool restriction. Tool restriction is used selectively (Planner) when the role's value depends on not writing — composition rules don't apply to all agents.

## What this repo does NOT have

- No package manager, no `package.json`, no `requirements.txt`, no language toolchain.
- No automated tests. Validation is `./plugins/tm/scripts/taskman.sh validate`, which checks frontmatter and folder/status consistency.
- No CI configuration in-tree.
- No `settings.json` at the repo root.
