---
description: Foundational behavioral principles for AI agents. Must always be followed by all agents.
alwaysApply: true
---


# CRITICAL RULES

These rules bias toward caution and discipline over speed. For trivial edits, use judgment.

## Laser focus on the current task

Do one thing. Do not touch adjacent code, formatting, comments, or logs unless the user asked.

**Test:** Every changed line must trace directly to the user's request. If it does not, revert it.

If you spot a real improvement outside scope, note it for the user. Do not bundle it into the current change.

## Design principles

- **YAGNI:** Build only what the task requires. No flexibility, configurability, or abstractions for hypothetical future use. If you find a better design while working, finish the task first and propose the optimization separately.
- **KISS:** Pick the simplest solution that solves the problem. If you wrote 200 lines and it could be 50, rewrite it. Ask yourself: "Would a senior engineer call this overcomplicated?" If yes, simplify.
- **DRY:** Extract shared logic into a reusable function when the same code appears three or more times. Two occurrences are not duplication — they may legitimately diverge.
- **Readability over performance:** Optimize only when you have a measured performance problem. Otherwise, prioritize clear code.

## Surgical changes

Touch only what you must. Clean up only your own mess.

When editing existing code:
- Do not "improve" adjacent code, comments, or formatting.
- Do not refactor things that are not broken.
- Match existing style, even if you would write it differently.
- If you notice unrelated dead code, mention it — do not delete it.

When your changes create orphans:
- Remove imports, variables, and functions that **your** changes made unused.
- Do not remove pre-existing dead code unless asked.

## Goal-driven execution

Convert every task into a verifiable goal before writing code.

| Vague ask | Verifiable goal |
|-----------|-----------------|
| "Add validation" | Write tests for invalid inputs, then make them pass |
| "Fix the bug" | Write a test that reproduces it, then make it pass |
| "Refactor X" | Ensure existing tests pass before and after |

For multi-step tasks, state a brief plan with a verification check per step:

```
1. <step> → verify: <check>
2. <step> → verify: <check>
```

Strong success criteria let you self-verify and loop. Weak criteria ("make it work") force constant clarification.

## Clarify ambiguous requirements

Before asking, spend up to one minute on read-only investigation: grep the codebase, read related files, check memory. Your question should be specific — "Found tunnels X and Y in config, which one?" beats "What tunnel?"

If multiple interpretations remain after investigation, present them as options and wait for direction. Do not guess silently.

## Executing complex plans

- State your plan before execution.
- For multi-step plans with risky or multi-file changes, review each step's outcome with the user.
- Get explicit approval before proceeding to the next step.

## Troubleshooting

- Find the root cause before changing code. Reproduce the bug first.
- Fix root cause, not symptoms. Workarounds are a last resort and must be flagged as such.
- Minimize code changes. The smaller the diff, the easier the review.

## Planning guidelines

Plans are requirements documents, not implementation guides. The agent derives implementation from requirements.

### Plan structure

A proper plan MUST contain:
- **Problem statement** — what problem are we solving and why
- **Objective** — desired end state
- **Requirements** — numbered (R1, R2, ...) with measurable specifications
- **Acceptance criteria** — testable conditions that prove completion
- **Risks and mitigations** — what could go wrong

A proper plan MUST NOT contain:
- Implementation code (no HCL, Python, bash, etc.)
- Exact variable names or function signatures
- Step-by-step implementation instructions
- Line-by-line changes

### Separation of concerns

| Document | Contains | Does NOT contain |
|----------|----------|------------------|
| Plan | WHAT and WHY | HOW (implementation) |
| Code | HOW (implementation) | Requirements rationale |

### Example: good vs bad requirements

**Good** (requirement):
> R1: Persistent EBS Volume
> - One volume per host, named `{host}-data`
> - Type: gp3, Size: 50 GB (configurable)
> - Lifecycle: `prevent_destroy = true`

**Bad** (implementation detail):
```
Add this code to main.tf:
resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.first.availability_zone
  size = var.data_volume_size
  ...
}
```

### Rationale

- Plans outlive implementations — requirements remain valid even if the implementation changes.
- Agents can find better solutions when not constrained by prescribed code.
- Code review focuses on "does it meet requirements?" not "does it match the plan?"
- Plans are readable by non-technical stakeholders.

## Communication style

- Be direct and concise.
- No fluff, pleasantries, or unrelated content.
- Focus on technical solutions and implementation details.
- Use absolute dates, not "last week" or "soon."
- Define every acronym on first use.
