# Feature plan — `taskman_agentic_cli` (Track A of `taskman_skill_integration`)

**Status:** Draft for review. Requirements-level; no implementation detail.
**Date:** 2026-06-09.
**Parent epic:** [`taskman_skill_integration`](./taskman_skill_integration.md).
**Why first:** these CLI capabilities are the precondition for Track B (skill rewire). Nothing in B can land until A exists.

---

## Problem statement

The Python `taskman` cannot yet be driven by the `/tm:*` agentic workflow. It writes only a `# {title}` body, has no way to delete a rejected draft, addresses items solely by 9-digit ID, cannot list a parent's children, does not validate body sections, and emits nothing on close for `/tm:docs-sync`. Until these gaps close, the skills cannot move off the bash `taskman.sh`.

## Objective

`taskman` exposes the operations the agentic loop needs — compose-and-persist a body, manage drafts, resolve human references to IDs, enumerate children, validate body sections, and signal doc-sync — each covered by tests. No skill or doc changes in this feature.

## Scope

**In scope:** additive changes to the `taskman` CLI and its tests (`taskman/src`, `taskman/tests`).
**Out of scope:** editing `/tm:*` skills, the planner, `CLAUDE.md`, or any doc outside `taskman/` (those are Tracks B and C). No change to the storage model, the name grammar, or the `task = leaf` invariant.

---

## Decisions resolved (recommendations from the epic plan, adopted — flag any to change)

- **R-A3 → adopt:** `new` gains an **optional `--slug`** that overrides the title-derived slug. Stable, meaningful references matter to skills.
- **R-A4 → adopt:** add a **`discard <id>`** command that deletes the item **only if `draft: true`**; refuses non-drafts. Parity with the bash flow.
- **R-A6 → adopt:** **no new selector syntax on mutators.** Standardize human-reference → ID resolution on the existing `list --json` and `search` outputs; this feature only guarantees those outputs are sufficient (expose `id`, `slug`, `path`, `status`, `type`, parent).
- **R-A7 → adopt:** **`validate` reports items missing required body sections** (per `rules/task-composition.md`), at **warning** severity so it never blocks the already-migrated tree. The planner stays the primary author-time enforcer.
- **R-A8 → adopt:** **`close` (and `move … done`) append a doc-sync entry** to `tasks/docs-sync-queue.txt` and print a "Doc-sync pending" line. Entry format becomes ID-based: `<ISO-8601> <type> <id>`. `/tm:docs-sync` (Track B) remains the reader.

---

## Requirements

- **R-A1 (body on stdin) — first slice, already decided.** When stdin is not a TTY, `taskman new` reads the work-item body from stdin and writes it as the item body in place of the default `# {title}`. With no piped stdin, behavior is unchanged.
- **R-A2 (parseable ID output).** `new` prints the created item's bare 9-digit ID in a stable, machine-parseable form (in addition to, or alongside, the path), so a skill can capture it and pass it to a later `--parent`.
- **R-A3 (optional slug).** `new --slug <s>` sets the slug explicitly (validated against the name grammar); without it, the slug is derived from the title as today.
- **R-A4 (discard).** `taskman discard <id>` removes a **draft** item; it refuses any item whose `draft` is not `true`, with a clear message.
- **R-A5 (child enumeration).** A supported command lists the children of a given item by ID — at minimum direct children with their `id`, `type`, `status`, `slug` — so a skill can iterate members and judge cascade readiness. `--json` supported.
- **R-A6 (resolution sufficiency).** `list --json` and `search --json` emit enough per item (`id`, `slug`, `title`, `status`, `type`, `path`, and parent linkage) for a skill to map a slug or path to a unique ID, or to detect ambiguity.
- **R-A7 (section validation).** `taskman validate` flags items missing the required body sections for their type (epic/feat/task) per `rules/task-composition.md`, at warning severity, with the item ID and the missing section names.
- **R-A8 (doc-sync signal).** On a successful transition to `done` (`close`, and `move <id> done`), `taskman` appends `<ISO-8601> <type> <id>` to `tasks/docs-sync-queue.txt` and prints a one-line "Doc-sync pending" hint. `list` prints a warning when the queue is non-empty.
- **R-A9 (regression safety).** Every change above ships with pytest coverage; the existing suite stays green; no change to existing command behavior beyond the additions named here.

---

## Acceptance criteria

1. `printf '## Problem statement\n...' | taskman new --title "X" --type task` creates an item whose body is the piped content, not `# X`; with no pipe, the body is `# X`. (R-A1)
2. The `new` invocation above prints a 9-digit ID that `taskman show <id>` resolves. (R-A2)
3. `taskman new --title "X" --slug my_slug` produces an item with slug `my_slug`; an invalid slug is rejected. (R-A3)
4. `taskman discard <draft-id>` deletes the item; `taskman discard <non-draft-id>` exits non-zero and changes nothing. (R-A4)
5. A command lists a parent's children by ID with status, in text and `--json`. (R-A5)
6. `taskman list --json` / `search --json` include `id`, `slug`, `title`, `status`, `type`, `path`, and parent linkage for each item. (R-A6)
7. `taskman validate` warns (not errors) on an item missing a required section, naming the item ID and the section. (R-A7)
8. After `taskman close <id>` (all descendants done) or `taskman move <id> done`, `tasks/docs-sync-queue.txt` has a new `<ts> <type> <id>` line and the command printed a "Doc-sync pending" hint. (R-A8)
9. `uv run pytest` in `taskman/` passes, including new tests for each requirement. (R-A9)

---

## Suggested build order

1. **R-A1** (stdin body) — self-contained, the unblocker; smallest valuable slice.
2. **R-A2 + R-A3** (ID output, optional slug) — round out `new`.
3. **R-A4** (discard) — completes the draft lifecycle.
4. **R-A5 + R-A6** (child listing, JSON sufficiency) — read-side for iteration/resolution.
5. **R-A7** (section validation) — safety net.
6. **R-A8** (doc-sync signal) — the one with the most surface; do last.

Each step lands with its tests before the next begins.

---

## Risks and mitigations

- **K-A1 — `new` output contract.** Adding ID output (R-A2) could break a caller that parses the current path-only output. *Mitigation:* additive output, documented; the only current callers are the skills (Track B) and tests.
- **K-A2 — doc-sync queue format change (R-A8).** The entry is now ID-based, not path/slug-based. *Mitigation:* Track B updates `/tm:docs-sync` to the new format in lockstep; until then the queue is informational.
- **K-A3 — section validation noise (R-A7).** Warning on the migrated tree could be loud. *Mitigation:* warning severity only; scope the check to non-done items if noise is high.
