# Taskman evaluation

Keep a short shared lifecycle contract and let the CLI enforce invariants. The trial did not demonstrate a need for a long validation skill, nor did it establish that skills are unnecessary for complex development.

## Shared-context pilot

Codex inspected Diviner's existing conversation contract and ran its bounded offline agent tests: 12 passed. A fresh Claude Code session read the Taskman records, inspected the referenced source, recorded an attributed review and closed its assigned QA task. Claude did not rerun those tests; it correctly distinguished static corroboration from independent execution. Diviner's working-tree status was unchanged after the review.

A fresh Codex session using official CLI 0.155.1 found the installed Taskman skill and recovered Claude Code's review. The earlier bundled client rejected its configured model; a temporary current client resolved that check without replacing the user's installation. The model selection was unchanged.

Browser checks confirmed UI creation, CLI readback, removal of the disposable draft, nested pilot tasks and display of the Claude Code journal. Automated tests cover UI-created epic decomposition into a story and dependent tasks, separate-process context recovery, verification and parent closure.

## Validation-skill ablation

Three fresh Claude Code sessions used the same default model, reported as `claude-sonnet-5`. Each received two isolated tasks: one Python assertion that passes and one that fails. Each was asked to execute the gates, record evidence, close only passing work and leave failed work open. Tool access, request and fixture structure were matched; instruction text varied. Skills were disabled for automatic loading and the tested text was supplied explicitly.

| Guidance | Passing task | Failing task | Close attempted before summary | Agent turns | Reported cost |
|---|---|---|---|---:|---:|
| Original validation skill | Closed with evidence | Open with failure evidence | Yes; CLI rejected it | 7 | $0.0763 |
| Compact Taskman contract | Closed with evidence | Open with failure evidence | No | 6 | $0.0470 |
| No validation skill | Closed with evidence | Open with failure evidence | Yes; CLI rejected it | 7 | $0.0588 |

All fixture files remained unchanged. [Recorded outcomes and usage](skill-evaluation.json) preserve the observations. Costs are CLI-reported estimates, not billing reconciliation. Initial trials were excluded because restrictive shell rules blocked compound commands; those runs do not measure skill quality.

This is one small trial per condition, not a statistical benchmark. Cache reuse and run ordering affect cost; the observed differences do not establish general savings. The task measured validation and lifecycle compliance, not design, decomposition quality, implementation, parallel agent coordination or long-running recovery. User configuration was retained across all sessions. The final packaged skill adds context routing around the compact contract and is not byte-for-byte the tested candidate.

## Changes and recommendations

- Retain define, design, plan, implement and validate as focused entrypoints sharing the Taskman-specific contract. Replace obsolete paths and commands; don't duplicate a general programming tutorial in each stage.
- Keep requirements, meaningful checks, immutable closure and evidence freshness enforced in the CLI. Both the original and no-skill trials recovered from the CLI's missing-summary rejection.
- Retain optional code review, documentation synchronization, commenting and commit skills. They were not ablated; there is no evidence here to justify removing them.
- Keep planner and QA roles available, without requiring a separate agent for every stage or assuming one host's delegation API.
- Do not add new skills yet. More complex project use should establish whether a concrete gap requires one.

The broader skill rewrites are compatibility and scope corrections, not experimentally proven quality gains. Next evidence should come from actual Taskman use on a bounded development change, preserving the distinction between testing Taskman and developing the testbed project.

## Final checks

The final Python suite passed 230 tests. All nine skill files passed schema validation, and the marketplace manifest passed Claude Code validation. A further fresh Claude Code run using the revised installed workflow closed the passing fixture with summary and evidence and left the failing fixture active with a failing verification. Its fixture files remained unchanged.

The browser showed the shared review, hierarchy and dependencies, with a rendered diagram and no horizontal page overflow. The installed-skill run used the shared reference from the cache; no Diviner development or deployment was performed.
