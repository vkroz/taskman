# QA: Mindset and Principles

These rules govern any agent (sub-agent or skill) performing validation of work output. Procedure lives in the validation skills; principles live here.

## Mindset

Validate against the spec, not the implementation. The agent who wrote the code already convinced themselves it works — QA's job is to verify it against the original requirements as a fresh reader would.

If a behavior is in the spec but missing in the result, that is a fail.
If a behavior is in the result but missing in the spec, that is also a fail — unstated scope creep.
If a behavior is in both, run it end-to-end against documented user paths.

## Role separation

QA runs in a fresh context. It receives:

- The spec document (vision, requirements, feature body, linked spec).
- The implementation result (running code, test output, artifact paths).
- The level of validation requested (task, feature, epic, product).

QA does **not** receive:

- Implementer reasoning (why a choice was made).
- The conversation transcript that led to the result.
- The detailed-design notes from `/tm:implement`.

Fresh context is the point. An implementer who read the spec, made tradeoffs, and built the code has already collapsed the "what could go wrong" space; a fresh reader sees gaps that closed space hid.

## What to check

1. **Spec coverage** — every numbered requirement, every acceptance criterion. Pass or fail per item.
2. **User paths** — happy path and 2-3 realistic failure paths per user-facing capability.
3. **Interfaces between components** — the seam where implementer reasoning is thinnest is where bugs concentrate.
4. **Regression risk** — prior features in the same area still work.

Skip: unit-level coverage (the implementer's tests own that); micro-optimizations (not QA's call).

## Severity

- **Critical** — blocks the documented user path; data loss; security breach.
- **Major** — degrades a documented user path; workaround exists but is non-obvious.
- **Minor** — cosmetic; unclear error message; recoverable inconsistency.

## Scope decisions

- Fix within QA scope: clear bugs in glue code, missing test cases, documentation drift surfaced by the result.
- Escalate to product owner: spec ambiguity, requirement conflicts, scope decisions, anything that needs a trade-off choice.

QA does not redesign. If the only way to fix is to redesign, escalate.

## Output

A short report. Per-requirement pass or fail. Per-severity issue list. One-line verdict — ready or not ready. Specific findings, not generic ones: *"Login fails on empty password — no validation; 500 error"* beats *"Form validation needs work."*
