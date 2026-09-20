---
name: code-comment
description: Add explanatory comments to code to improve readability and maintainability. Use when the user asks to "comment code", "add comments", "document this code", "explain this code with comments", or wants inline documentation added to source files.
---

# Code Comment

Add meaningful comments to source code without altering its behavior.

## Workflow

1. **Determine scope** — identify what the user wants commented: a specific function, a file, a selection, or a set of files.
2. **Read the code** — understand the logic, control flow, and data structures before writing anything.
3. **Add comments** — apply the rules below, using the correct comment syntax for the language.
4. **Verify** — confirm the code is unchanged aside from added comments. Run linters if available.

## What to Comment

- **Why**, not **what** — explain intent, trade-offs, and constraints the code itself cannot convey.
- Non-obvious algorithms or business logic.
- Assumptions, invariants, and known limitations.
- Important side effects (I/O, mutations, external calls).
- Edge cases and error-handling rationale.
- Public API surface: function/class purpose, parameters, return values.

## What NOT to Comment

- Obvious operations (`i += 1  // increment i`).
- Code that is self-documenting through clear naming.
- Restating the code in English (`// loop through items` above a for-loop).
- Commented-out code — remove dead code instead.
- Change history — that belongs in version control.

## Comment Style

- Use the language's idiomatic comment syntax and doc-comment conventions:
  - Python: `#` inline, `"""docstrings"""` for modules/classes/functions
  - JS/TS: `//` inline, `/** JSDoc */` for exports
  - Go: `//` with godoc conventions
  - Rust: `///` doc-comments, `//` inline
  - Java/Kotlin: `/** Javadoc */` for public API, `//` inline
  - Other languages: follow their standard conventions
- Keep comments concise — one or two sentences per block.
- Place comments on the line above the code they describe, at the same indentation level.
- For multi-line explanations, use a block/doc-comment rather than stacking single-line comments.

## Safety Rules

- NEVER modify code logic, formatting, or structure — only add comments.
- NEVER remove or rewrite existing comments unless they are demonstrably wrong.
- Preserve all original whitespace and line breaks.

## Examples

**Good:**

```python
# Retry with exponential backoff to avoid overwhelming a recovering service
for attempt in range(max_retries):
    wait = base_delay * (2 ** attempt)
    time.sleep(wait)
```

**Bad:**

```python
# Loop from 0 to max_retries
for attempt in range(max_retries):
    # Calculate wait time
    wait = base_delay * (2 ** attempt)
    # Sleep for wait seconds
    time.sleep(wait)
```
