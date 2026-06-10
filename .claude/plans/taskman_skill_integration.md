# Epic plan — `taskman_skill_integration`

**Status:** Draft for review (not yet a tracked work item). Requirements-level; no implementation detail.
**Date:** 2026-06-09.
**Predecessor:** epic `taskman_python` (done) shipped the Python `taskman` CLI and ran `migrate` on this repo's task tree.
**Predecessor (this session):** Part 1 documented the repo-less `uv tool install` of the Python `taskman`.

---

## Problem statement

The `/agn:*` skills, the planner sub-agent, and the model docs (`CLAUDE.md`, `rules/task-composition.md`, `docs/specification.md`) are still wired to the **deprecated bash `taskman.sh`** and its **flat, slug/path-addressed, four-tier model**. The repo's actual task tree was already migrated to the Python `taskman`'s **uniform recursive, ID-addressed model** (name-encoded nested directories). The two halves no longer match.

Concrete consequences:

1. Skills invoke `./scripts/taskman.sh` — a repo-relative path that does not exist for a marketplace-installed user, so `/agn:*` task operations fail off-repo.
2. Even with the Python `taskman` installed (Part 1), the skills cannot drive it: the CLI is missing capabilities the skills depend on, and the two disagree on how items are identified, created, linked, and closed.
3. The model docs describe a storage layout and tier ladder that no longer exist.

### Capability gap (bash assumption → Python `taskman` today)

| Skills depend on (bash) | Python `taskman` today | Nature of gap |
|---|---|---|
| `new … ` with a Planner-composed body on **stdin** | `new --title --type --parent`; body is `# {title}` only; no stdin | **Missing capability** |
| `--slug`, `--epic <slug>`, `--feature <slug>`, `--kind bug` on create | slug derived from title; parent = `--parent <ID>`; no `kind` | Surface mismatch |
| `discard <path>` for rejected drafts | no delete/discard command | Missing capability |
| Items identified by **path** / **slug** | items identified by **9-digit ID** only | Addressing mismatch |
| `feature close <slug>` / `epic close <slug>` | unified `close <id>` (enforces all-descendants-done) | Verb + selector mismatch |
| `list tasks --feature <slug>` (enumerate a parent's children) | `list` filters by status/type only — no parent filter | Missing capability |
| section validation at `new`; `## Summary`-before-done | `validate` checks name/YAML grammar, not body content | Validation gap |
| docs-sync queue + "Doc-sync pending" emitted on close | nothing emitted on close | Missing hook |
| flat `tasks/{epics,features,backlog,active,done}/YYYYMMDD_slug.md`; `epic:`/`feature:`/`kind:` fields; fixed `product→epic→feature→task` ladder | nested `<priority>.<type>-<id>.<status>.<slug>/`; nesting *is* the parent link; `feature`→`feat`; uniform recursive | Model mismatch |

---

## Objective

The `/agn:*` skills drive the Python `taskman` end-to-end — for both in-repo (dogfooding) and marketplace-installed users — against the uniform recursive, ID-addressed model. The bash `taskman.sh` is retired. The model docs describe the model that actually ships.

---

## Scope

**In scope**

- **Track A** — extend the Python `taskman` CLI with the capabilities the agentic workflow needs.
- **Track B** — rewire the six `/agn:*` skills and the planner to the extended CLI and the uniform model.
- **Track C** — reconcile the model docs (`CLAUDE.md`, `rules/task-composition.md`, `docs/specification.md`, READMEs) and retire `taskman.sh`.

**Out of scope**

- New `taskman` features beyond what the skills require (web UI changes, lifting the `task = leaf` invariant, a real priority scheme, `depends_on` authoring UX).
- Re-running `migrate` (already done; the tree is in the new model).
- Publishing `taskman` to PyPI or Homebrew (deferred indefinitely by the `taskman_python` epic).
- The `product` tier, which writes `docs/` and never touched `taskman` — unchanged.

---

## Decisions taken

- **D1 — Body composition (resolved):** `taskman new` will **accept the work-item body on stdin** (see R-A1). This keeps the Planner→compose→persist flow close to today's and minimizes skill-side file surgery.
- **D2 — Plan deliverable (resolved):** this document. The epic is created in the new model later, once Track A lands (see Risk K1).

---

## Requirements

### Track A — Python `taskman` CLI extensions

- **R-A1 (body on stdin):** `taskman new` reads the work-item body from stdin when stdin is not a TTY; the supplied body replaces the default `# {title}` body. With no stdin, behavior is unchanged.
- **R-A2 (stable ID output):** every create/mutation command emits the affected item's stable 9-digit ID in a parseable form, so a skill can capture a parent's ID and pass it to a subsequent `--parent` create.
- **R-A3 (explicit slug — DECISION):** decide whether `new` accepts an optional explicit slug for stable, human-meaningful references, or whether title-derived slugs are sufficient. Requirement holds either way: a created item's slug is predictable enough for a skill to reference it.
- **R-A4 (discard — DECISION):** a rejected draft can be removed through a supported path — either a CLI `discard`/`delete` that refuses non-drafts, or an agreed alternative. No skill may delete task files by raw `rm`.
- **R-A5 (child enumeration):** a skill can list the direct (and, where needed, transitive) children of a given item by ID, with status, to iterate members and report cascade readiness.
- **R-A6 (selector resolution — DECISION):** a documented, reliable way to map a slug or a user-supplied path to a stable ID — either the CLI accepts slug selectors, or skills resolve via `--json` listings / `search`. One mechanism, documented once.
- **R-A7 (body-section validation — DECISION):** the required body sections defined in `rules/task-composition.md` are validated by a supported step (extended `taskman validate`, a create-time check, or an explicit skill gate). The chosen home is documented.
- **R-A8 (docs-sync signal — DECISION):** closing/finishing an item surfaces a "docs-sync pending" signal that `/agn:docs-sync` consumes — reimplemented in `taskman`, replaced by a Claude Code hook, or deliberately dropped. The decision is recorded; `/agn:docs-sync` is updated to match.
- **R-A9 (regression safety):** every Track A change ships with pytest coverage; the existing 200+ test suite stays green.

### Track B — skill + planner rewire

- **R-B1 (invocation):** skills invoke the bare `taskman` command (on PATH via `uv tool install`), not `./scripts/taskman.sh`.
- **R-B2 (ID addressing):** skills address items by 9-digit ID. Where a skill receives a path or slug (for example `/agn:implement <id>`), it resolves to an ID via the R-A6 mechanism.
- **R-B3 (create surface):** create flows use the new `new` surface (stdin body, `--type`, `--parent <id>`, optional slug per R-A3); references to `--epic`, `--feature`, and `--kind` are removed. Parent linkage is `--parent <id>` plus nesting.
- **R-B4 (close surface):** close flows use unified `close <id>`, relying on its all-descendants-done precondition; `feature close <slug>` / `epic close <slug>` are removed.
- **R-B5 (draft lifecycle):** create → preview-from-disk → `finalize` / discard (per R-A4). No file is left with `draft: true`.
- **R-B6 (summary):** the mandatory `## Summary` before an item moves to `done` remains skill-enforced (appended before `close`).
- **R-B7 (tier mapping — DECISION, load-bearing):** decide how the four-tier skill surface (`/agn:define|implement|validate <product|epic|feature|task>`, the planner's level awareness) maps onto the **uniform** model. Options: keep the tier vocabulary as `--type` labels + nesting depth; or simplify the surface toward uniform work items. This choice shapes most of Track B and must be resolved first.
- **R-B8 (planner brief):** the planner's per-level guidance and its emitted decomposition are updated for the uniform model — no `epic:`/`feature:` linkage fields; parent expressed by ID/nesting.

### Track C — docs reconcile + bash retirement

- **R-C1 (model docs):** `CLAUDE.md`, `rules/task-composition.md`, and `docs/specification.md` describe the uniform recursive model — name grammar, 9-digit IDs, nesting as linkage, status encoded in name + YAML, `depends_on`. All flat-folder, four-tier-ladder, `epic:`/`feature:`/`kind:`, and draft-folder assumptions are removed.
- **R-C2 (locations + hierarchy):** file-location tables and the README "work-unit hierarchy" reflect the new layout and the uniform model.
- **R-C3 (retire bash):** `plugins/agn/scripts/taskman.sh` is deleted once no skill or doc references it; the `taskman.sh is the only writer` framing is rewritten around the Python CLI.
- **R-C4 (close the loop):** the Part 1 README "skill integration in progress" caveat is removed/finalized once `/agn:*` work end-to-end on a fresh install.

---

## Acceptance criteria

1. **Create:** on a fresh install (plugin + `uv tool install` taskman), `/agn:define <task-tier>` creates a work item via Python `taskman` carrying the Planner-composed body, in the nested name-encoded layout.
2. **Lifecycle:** `/agn:implement` moves an item backlog→active→done by ID; a parent `close` succeeds only when all descendants are done, and is invoked by the skills without `feature/epic close <slug>`.
3. **Validate:** `taskman validate` passes on the resulting tree; required body sections are enforced per the R-A7 decision.
4. **No legacy references:** no file under `plugins/agn/`, `docs/`, or `CLAUDE.md` references `taskman.sh`, the flat `tasks/{epics,features,backlog,active,done}` layout, or `epic:`/`feature:`/`kind:` fields (historical `tasks/**` records excepted).
5. **Docs-sync:** `/agn:docs-sync` functions per the R-A8 decision.
6. **Docs match reality:** `CLAUDE.md`, `rules/task-composition.md`, and `docs/specification.md` describe the uniform recursive model and agree with `taskman help`.
7. **Tests green:** `taskman`'s pytest suite passes, including new Track A coverage.

---

## Proposed decomposition (linked features, in order)

1. **`taskman_agentic_cli` (Track A)** — CLI extensions: stdin body (R-A1), ID output (R-A2), slug (R-A3), discard (R-A4), child enumeration (R-A5), selector resolution (R-A6), section validation (R-A7), docs-sync signal (R-A8), tests (R-A9). *Unblocks Track B.*
2. **`skill_rewire_to_python_taskman` (Track B)** — rewire the six skills + planner (R-B1…R-B8). *Gated on the R-B7 tier-mapping decision.*
3. **`model_docs_and_bash_retirement` (Track C)** — reconcile docs, retire bash, close the README caveat (R-C1…R-C4).

Order rationale: A unblocks B (skills need the capabilities to exist); C lands after B passes acceptance so the bash fallback survives the migration.

---

## Risks and mitigations

- **K1 — Circular dogfooding.** Decomposing this epic with the current methodology writes work items in the model being replaced. *Mitigation:* keep this plan as a plain doc; create the epic/features in the new model via Python `taskman` once Track A (notably R-A1) lands.
- **K2 — Tier-surface design (R-B7) is the dominant unknown.** Mapping four tiers onto a uniform model touches most of Track B. *Mitigation:* resolve R-B7 before starting Track B; spike one skill end-to-end first.
- **K3 — Interim breakage.** During migration, `/agn:*` may be partially broken in-repo. *Mitigation:* retain `taskman.sh` until Track B passes acceptance; retire only in Track C.
- **K4 — Validation relocation (R-A7).** Moving section validation off `new` could regress body quality. *Mitigation:* explicit validate coverage and an acceptance check.
- **K5 — docs-sync reimplementation (R-A8).** Re-creating the PostClose hook in Python is non-trivial; dropping it loses a feature. *Mitigation:* treat as an explicit, scoped decision with its own acceptance criterion.

---

## Open decisions to resolve (carried as requirements above)

| # | Decision | Recommendation |
|---|----------|----------------|
| R-A3 | Explicit `--slug` on `new`? | Accept an optional slug — stable references matter to skills. |
| R-A4 | How to discard a draft? | Add a CLI `discard <id>` that refuses non-drafts (parity with bash). |
| R-A6 | Slug/path → ID resolution? | Standardize on `--json` listings + `search`; document the pattern once. |
| R-A7 | Where do body sections get validated? | Extend `taskman validate` to cover sections — one tool, agent-checkable. |
| R-A8 | docs-sync hook? | Re-emit a queue entry on `close` in `taskman`; keep `/agn:docs-sync` as the reader. |
| R-B7 | Four tiers vs uniform model? | Keep the tier vocabulary as `--type` labels + nesting; do not re-cut the skill surface in this epic. |

These are product/API decisions for `taskman`; they are listed for the owner to confirm before the corresponding feature starts.
