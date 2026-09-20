# Doc Maintenance: When and What to Update on Closure

Whenever a work unit (task, feature, epic) closes, the agent reviews upstream documents for drift and proposes diffs. The user reviews diffs before commit.

## What to check

Check these documents in this dependency order:

1. `docs/vision.md` — does the closed work change product direction or scope? (rare)
2. `docs/spec.md` — does it add, remove, or change a user-facing capability described here?
3. `docs/requirements.md` — does it change a formal requirement (R-numbered)?
4. `docs/architecture.md` — does it add, remove, or change a system component, integration, or technology choice?
5. Linked spec under `docs/<area>/.../-spec.md` — if the work was scoped to one of these.

A change downstream often requires a matching update upstream — a new architecture component should be reflected in the spec; a removed user flow should be reflected in vision.

## When to propose an update

Propose a diff when **either** of these is true after the work closes:

- The doc states a behavior or intent that the running code no longer matches.
- The doc is missing a behavior or intent that the running code now exhibits.

Do not propose updates for:

- Implementation details (file paths, function names, internal classes) — those belong in code comments, not product docs.
- Speculative future state — only document what shipped.
- Cosmetic prose edits unrelated to the closure.

## How to propose

Show the diff: file, before, after. Explain why in one sentence. Wait for user approval. Do not commit. The user owns the decision on whether the diff lands.

## When no doc update is needed

State that explicitly: *"No doc updates needed — closure does not affect vision, spec, requirements, or architecture."* This is a valid and common outcome. Silence is worse than a deliberate no-op note.
