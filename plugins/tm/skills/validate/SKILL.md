---
name: validate
description: Quality gates at any tier — task, feature, epic, or product. Invoke with /tm:validate <level>. Task runs the task's own ## Quality gates in the main session (lightweight). Feature, epic, and product delegate to the QA sub-agent (fresh-context validation against spec).
argument-hint: task | feature | epic | product
---

# Validate (`/tm:validate <level>`)

## Arguments

| Position | Variable | Value |
|----------|----------|-------|
| `$0` | level | `task`, `feature`, `epic`, or `product` |
| `$1` | id | Optional task path (for `task`) or scope hint (for `feature` / `epic`) |

## Argument validation

If `$0` is missing, stop and ask:
> *"What level — task, feature, epic, or product?"*

If `$0` is not one of `task`, `feature`, `epic`, `product`, stop and list the valid set.

## Dispatch

Read `$0`. Run exactly one of the branches below.

## Validation via the QA sub-agent

For `feature`, `epic`, and `product` levels, validation runs in the QA sub-agent (`plugins/tm/agents/qa.md`). The sub-agent loads `rules/qa.md`, reads the spec and the implementation, runs tests, and returns a verdict. Fresh context is the point — the agent that wrote the code has already collapsed the design space in their head; a fresh reader sees gaps that closed space hid.

The `task` branch does **not** invoke the QA sub-agent — the task's own `## Quality gates` are narrow and the parent session has the right context to run them.

When a workflow below says "delegate to QA", invoke it via the Agent tool with `subagent_type: qa` and a brief containing:

- `level` — `feature` | `epic` | `product`
- `scope` — slug (feature/epic) or "whole product"
- `spec_paths` — paths to relevant documents (`docs/vision.md`, `docs/spec.md`, `docs/requirements.md`, `docs/architecture.md`, parent epic/feature file, linked spec)
- `implementation_paths` — files, test commands, dev-server URLs, sample data locations
- `regression_scope` (optional) — adjacent features/areas to re-check

QA returns a structured response (`## Verdict`, `## Per-requirement results`, `## Issues` by severity, `## What I fixed`, `## What I escalated`, `## Report` path, `## Next steps`). Surface the verdict to the user. On `not ready`, present the issues and stop — do not silently advance the lifecycle (e.g., do not auto-close a feature whose QA verdict was `not ready`).

---

## $0 = task

### Preconditions

A task file at `$1` (path) — either in `tasks/active/` or `tasks/backlog/`. The task body must include a `## Quality gates` section listing the validation steps. If the task is in `done/`, validation has already run; warn the user and ask if they really want to re-run.

### Workflow

1. **Read the task** — Load `$1`. Extract the `## Quality gates` content.

2. **Run each gate** — For each gate item, execute the named command or perform the named check. If the gate is prose (e.g., "verify behavior X manually"), surface it to the user and wait for them to confirm.

3. **Report** — Per-gate pass/fail. If any gate fails, name the failure specifically (command, exit code, observed behavior). Do not aggregate into a vague "tests failed" line.

4. **Offer next step (do not auto-execute)** — If all gates pass:
   - If the task is in `tasks/active/`, offer: *"All gates pass. Run `./scripts/taskman.sh move <path> done`?"*
   - If the task is in `tasks/backlog/`, offer nothing — the user runs `/tm:implement task` for activation.
   - In both cases, the user owns the decision to move state.

5. **Stop.** Do not advance the task lifecycle without user instruction.

### Discipline

- **No QA sub-agent at this level.** The gates are narrow and named in the task body; the parent session has the right context to run them. Delegating would lose that context for negligible benefit.
- **Do not modify the task file** beyond optionally appending a one-line note to its `## Quality gates` section if a gate is incorrect — and only with user approval. The `## Summary` is owned by `/tm:implement task`, not by this skill.
- **Failures are reports, not fixes.** If a gate exposes a bug, file a bug task via `/tm:define task --kind bug --feature <slug>` — do not fix inline.

---

## $0 = feature

### Preconditions

- A feature slug or recent feature context. If `$1` is empty, infer from the most recent active/just-closed feature in `tasks/features/` or ask the user.
- A runnable app or test environment the project uses (tests, dev server, etc.).
- Enough implementation exists to exercise the scope (otherwise say what is missing).

### Workflow

1. **Resolve scope** — Identify the feature slug. Read the feature file at `tasks/features/YYYYMMDD_<slug>.md` and its linked spec (if any) under `docs/<area>/.../-spec.md`.

2. **Gather paths** — Collect:
   - `spec_paths` — feature file, linked spec, related `docs/spec.md` sections.
   - `implementation_paths` — source files changed for this feature (use `git log --name-only --grep <slug>` or ask user), test command(s), dev-server URLs.
   - `regression_scope` — adjacent features or areas the user names as at-risk (optional).

3. **Delegate to QA.** Per the **Validation via the QA sub-agent** section above, invoke with `level=feature` and the gathered paths.

4. **Surface verdict** — Present QA's `## Verdict`, `## Per-requirement results`, and `## Issues` to the user. Name the report path so they can open it.

5. **Halt on not ready** — If verdict is `not ready`, stop. Do not offer `feature close`. Do not advance lifecycle. The user owns the decision to triage the issues (route to `/tm:define task --kind bug --feature <slug>` for fixes, or escalate spec changes).

6. **Offer close on ready** — If verdict is `ready` and all member tasks are in `done`, offer: *"Run `./scripts/taskman.sh feature close <slug>`?"*. Do not auto-execute.

### Discipline

- Do not claim release readiness — that is `/tm:validate product`.
- The QA sub-agent's report is the authoritative artifact. Do not paraphrase or re-summarize beyond what the user needs to triage.
- If QA escalates, surface the escalation verbatim with QA's recommended next steps. Do not silently filter.

---

## $0 = epic

### Preconditions

- An epic slug. If `$1` is empty, ask the user or list candidates: `./scripts/taskman.sh list epics`.
- Every linked feature must already be closed (`status: done`). If any are still active/backlog, the epic is not ready for epic-level validation — direct the user to finish them first via `/tm:implement feature`.

### Workflow

1. **Read the epic** — Load `tasks/epics/YYYYMMDD_<slug>.md`. Read every linked feature file. Note the epic's `## Acceptance criteria` — these are the cross-feature conditions the QA sub-agent will check.

2. **Gather paths** — Collect:
   - `spec_paths` — epic file, every linked feature file, relevant sections of `docs/spec.md` and `docs/architecture.md`.
   - `implementation_paths` — source areas covered by the epic; full test suite command(s); integration scenarios.
   - `regression_scope` — features outside this epic that share components or interfaces.

3. **Delegate to QA.** Per the **Validation via the QA sub-agent** section above, invoke with `level=epic` and the gathered paths.

4. **Surface verdict** — Present QA's response to the user. Highlight cross-feature findings (the seams between member features are where epic-level bugs concentrate).

5. **Halt on not ready** — Same as feature branch: stop, do not offer `epic close`, let the user triage.

6. **Offer close on ready** — If verdict is `ready`, offer: *"Run `./scripts/taskman.sh epic close <slug>`?"*. Do not auto-execute.

### Discipline

- Epic validation is about cross-feature interactions, not re-running per-feature tests. Trust prior `/tm:validate feature` reports if they are recent and the relevant features have not changed since.
- The QA sub-agent's report is the authoritative artifact.

---

## $0 = product

### Preconditions

- `docs/spec.md` and `docs/requirements.md` (or equivalent product docs) exist.
- Implementation is intended to be **feature-complete** for the release under test.

If docs are missing, say so and ask whether to proceed with a reduced checklist.

### Workflow

1. **Gather paths** — Collect:
   - `spec_paths` — `docs/vision.md`, `docs/spec.md`, `docs/requirements.md`, `docs/architecture.md`, plus any per-area specs under `docs/<area>/.../-spec.md` that bound user-visible behavior.
   - `implementation_paths` — the running app or build artifact; full test suite command(s); end-to-end test scenarios (or a path to the suite that hosts them).
   - `regression_scope` — *"whole product"*. No subset.

2. **Delegate to QA.** Per the **Validation via the QA sub-agent** section above, invoke with `level=product` and the gathered paths. Tell QA to categorize findings as Critical / Major / Minor per `rules/qa.md`.

3. **Surface verdict** — Present QA's response to the user. The report file (typically `docs/system/system-test-report.md`) is the artifact for sign-off; name its path prominently.

4. **Halt on not ready** — Stop. Do not declare release readiness. The user owns the sign-off decision.

5. **Re-run loop** — If the user addresses findings and asks for re-validation, re-invoke QA with the same brief. Each run produces an updated report.

6. **Sign-off gate** — Release readiness is a user decision, not a QA verdict. QA can recommend `ready`; the user owns the call.

### Discipline

- The QA sub-agent's report is the release artifact. Do not paraphrase it in a way that loses Critical/Major/Minor categorization.
- If QA escalates spec ambiguities, surface them verbatim — these are signals that `docs/spec.md` or `docs/requirements.md` may need a revision via `/tm:design product`.
