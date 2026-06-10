# agn — Agentic SDLC

Agentic software-delivery loop for Claude Code. Claude facilitates writing specs and designing architecture, then autonomously builds implementation plans, implements, and tests — all through structured `/agn:*` skills with built-in review gates and document consistency.

## Install

From the `agenture` marketplace:

```
/plugin marketplace add AgentureHQ/agenture-loop
/plugin install agn@agenture
```

### Prerequisite: the `taskman` CLI

The `/agn:*` skills drive a task CLI named `taskman` — a standalone Python app, not bundled in the plugin. Install it once; no repo checkout needed:

```
uv tool install "git+https://github.com/AgentureHQ/agenture-loop.git#subdirectory=taskman"
```

Verify with `taskman version`. If the command isn't found afterward, run `uv tool update-shell` and restart the shell. `uv` provisions Python 3.11+ if needed; `pipx`/`pip` work too. Full notes in [`taskman/README.md`](../../taskman/README.md).

> **Migration in progress.** Skills are being rewired from the legacy bundled bash `scripts/taskman.sh` to this Python `taskman` (epic `taskman_skill_integration`). Until that lands, the skill call sites still target the bash script.

## Work-unit hierarchy

The plugin supports a four-tier hierarchy. Each tier is optional one level up — a task may have no feature, and a feature may have no epic.

```
product → epic → feature → task
```

| Tier | Purpose | File location |
|------|---------|---------------|
| **Product** | Vision, specification, requirements | `docs/vision.md`, `docs/spec.md`, `docs/requirements.md` |
| **Epic** | Functional block larger than a feature; decomposes into features | `tasks/epics/YYYYMMDD_<slug>.md` |
| **Feature** | Named product initiative; produces a plan and child tasks | `tasks/features/YYYYMMDD_<slug>.md` |
| **Task** | Unit of implementation work | `tasks/<state>/YYYYMMDD_<slug>.md` (where state is `backlog`, `active`, or `done`) |

Bugs are tasks with `kind: bug`. Same lifecycle. Can be attached to a feature or ad-hoc.

See `rules/task-composition.md` for the body and frontmatter model; see `./scripts/taskman.sh help` for the persistence model (storage, naming, lifecycle).

## Skills

The plugin ships 8 skills. Lifecycle skills follow the verb-noun pattern `/agn:<verb> <level>`; tool skills retain their action-named form.

### SDLC workflow (lifecycle skills)

| Verb | Skill | What it does |
|------|-------|--------------|
| Define | `/agn:define <product\|epic\|feature\|task>` | Define a work unit at the named tier — vision/spec/requirements (product), epic + linked features (epic), feature + linked tasks (feature), task/bug ticket (task). Delegates composition to the Planner sub-agent for epic/feature/task levels. |
| Design | `/agn:design <product\|epic\|feature>` | Focused revision of an existing unit's design; product produces `docs/architecture.md`. Epic and feature delegate to the Planner sub-agent for in-place body refinement. |
| Plan | `/agn:plan <epic\|feature>` | Focused revision of an existing unit's decomposition (epic into features, feature into tasks). Delegates to the Planner sub-agent in refine + plan-only mode. |
| Implement | `/agn:implement <task\|feature\|epic>` | Execute implementation; task = detailed design → code → tests; feature/epic = iterate children with review gates |
| Validate | `/agn:validate <task\|feature\|epic\|product>` | Quality gates. Task runs the task's own `## Quality gates` in the main session. Feature, epic, and product delegate to the QA sub-agent for fresh-context validation against spec. |

### Code and maintenance

| Skill | What it does |
|-------|--------------|
| `/agn:code-review` | Read-only codebase audit; produces backlog tasks |
| `/agn:code-comment` | Add explanatory comments to source code |
| `/agn:code-commit` | Stage files and write a well-formed git commit message |
| `/agn:docs-sync` | Process the doc-sync queue: review upstream docs for drift against recently closed units, propose diffs, apply after user approval, clear processed entries |

## Workflows supported

- **New product** — `/agn:define product` → `/agn:design product` → `/agn:define epic` (or `/agn:define feature` if no epic tier needed) → `/agn:implement epic` / `/agn:implement feature` → `/agn:validate product`
- **Bug fix** — `/agn:define task bug` → `/agn:implement task` → `/agn:validate feature` → `/agn:validate product`
- **Ad-hoc maintenance** — `/agn:define task` → `/agn:implement task` → `/agn:validate feature`
- **Incremental feature** — `/agn:define feature` (against existing product docs) → `/agn:implement feature` → `/agn:validate feature`
- **Codebase optimization** — `/agn:code-review` → `/agn:define feature` (or `/agn:define epic` for larger scope) → `/agn:implement feature` → `/agn:validate product`

## taskman.sh — task and feature CLI

All create / move / close / list operations on epics, features, and tasks go through `scripts/taskman.sh`. Skills compose content in dialog with the user, then hand off to taskman as the save step. Do not write task files directly.

```bash
./scripts/taskman.sh help                                # full surface
./scripts/taskman.sh new epic    --slug <s> --title T < body
./scripts/taskman.sh new feature --slug <s> --title T [--epic <s>] < body
./scripts/taskman.sh new task    --title T [--feature <s>] [--kind task|bug] < body
./scripts/taskman.sh finalize <path>                     # clear draft: true
./scripts/taskman.sh discard  <path>                     # delete a draft
./scripts/taskman.sh move <task-path> <backlog|active|done>
./scripts/taskman.sh list epics    [--status backlog|active|done]
./scripts/taskman.sh list features [--epic <s>] [--status backlog|active|done]
./scripts/taskman.sh list tasks    [--feature <s>] [--status backlog|active|done] [--kind task|bug]
./scripts/taskman.sh epic show    <slug>
./scripts/taskman.sh epic close   <slug>                 # fails unless all member features in done
./scripts/taskman.sh feature show  <slug>
./scripts/taskman.sh feature close <slug>                # fails unless all member tasks in done
./scripts/taskman.sh validate
```

`epic close` and `feature close` enforce the lifecycle rule: an epic can only close when every feature with matching `epic: <slug>` is in `done`, and a feature can only close when every task with matching `feature: <slug>` is in `done`.

## Project layout created by workflows

```
docs/vision.md
docs/spec.md
docs/requirements.md
docs/architecture.md
docs/<area>/.../-spec.md         # feature-scoped specs
tasks/
  epics/                          # YYYYMMDD_<slug>.md
  features/                       # YYYYMMDD_<slug>.md
  backlog/                        # YYYYMMDD[_NN]_<slug>.md
  active/
  done/
  gaps/                           # YYYYMMDD-HHMMSS_<task-slug>.md — design-gap escalation log
  docs-sync-queue.txt             # PostClose hook queue; processed by /agn:docs-sync
```

Created incrementally as you run each stage — no upfront scaffolding.

## Loading rules into your project

Plugins do not auto-load `rules/`. To activate the behavioral rules in your project, append this block to your project's `CLAUDE.md`:

```
@~/.claude/plugins/cache/agenture/agn/0.1.0/rules/first-principles.md
@~/.claude/plugins/cache/agenture/agn/0.1.0/rules/task-composition.md
@~/.claude/plugins/cache/agenture/agn/0.1.0/rules/writing-guideline.md
@~/.claude/plugins/cache/agenture/agn/0.1.0/rules/qa.md
@~/.claude/plugins/cache/agenture/agn/0.1.0/rules/doc-maintenance.md
```

Update the version (`0.1.0`) when you upgrade the plugin.

The five files:

- `first-principles.md` — design discipline (YAGNI, KISS, DRY), surgical changes, goal-driven execution. Always-on.
- `task-composition.md` — epic/feature/task frontmatter shapes, body section requirements, completion-summary template. Loaded by any skill that composes work units.
- `writing-guideline.md` — crisp, no-fluff prose style. Loaded by document-writing skills.
- `qa.md` — QA mindset, role separation, validation principles. Loaded by the QA sub-agent and `/agn:validate` skills.
- `doc-maintenance.md` — what to check after a work unit closes. Loaded by the docs-sync skill / PostClose hook.

Persistence rules (storage layout, naming, lifecycle preconditions, CLI surface, validation behavior) live in `./scripts/taskman.sh help` — the script is the single writer and the authoritative reference.

## PostClose hook and docs-sync

Every successful close action — `taskman.sh move <path> done`, `feature close <slug>`, `epic close <slug>` — appends an entry to `tasks/docs-sync-queue.txt` and prints a "Doc-sync pending" hint. Run `/agn:docs-sync` to process the queue: it walks the dependency chain (vision → spec → requirements → architecture → linked spec) per `rules/doc-maintenance.md`, proposes diffs, applies after user approval, and clears each processed entry.

The queue is durable on disk. `taskman.sh list` prints a WARN hint whenever the queue is non-empty, so you see pending entries during normal task-state checks.

### Optional: surface the queue at session start

For automatic surfacing of pending entries when a new Claude Code session opens in this project, add a SessionStart hook to your project's `.claude/settings.json` (or your user-level `~/.claude/settings.json`):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "test -s tasks/docs-sync-queue.txt && printf 'Doc-sync pending: %s entries in tasks/docs-sync-queue.txt. Run /agn:docs-sync.\\n' \"$(wc -l < tasks/docs-sync-queue.txt | tr -d ' ')\" || true"
          }
        ]
      }
    ]
  }
}
```

The hook is opt-in — the plugin does not install it for you. Without it, the queue still persists; you just see the hint via `taskman.sh list` rather than at session start.

## Plugin layout

```
plugins/agn/
├── .claude-plugin/plugin.json    # plugin manifest
├── skills/                       # 9 /agn:* skills
├── agents/                       # planner (Design + Plan); qa (fresh-context validator)
├── rules/                        # first-principles, task-composition, writing-guideline, qa, doc-maintenance
├── scripts/taskman.sh            # task lifecycle CLI; PostClose hook emits to docs-sync-queue.txt
└── README.md
```
