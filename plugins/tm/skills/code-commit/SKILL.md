---
name: code-commit
description: This skill should be used when the user asks to "commit", "commit changes", "create a commit", "stage and commit", "git commit", or wants to save their work to version control. Guides Claude through staging files and writing a well-formed git commit message.
---

# Code Commit

Guide for creating a git commit with proper staging and a meaningful commit message.

## When This Skill Applies

Activate when the user wants to:
- Commit staged or unstaged changes
- Save work to version control
- Create a git commit with a descriptive message

## Pre-Commit Checklist

Before committing, always:
1. Run `git status` to see what files have changed
2. Run `git diff` (staged and unstaged) to review the changes
3. Run `git log --oneline -5` to understand the commit message style in this repo

## Staging Files

- Stage specific files by name rather than `git add -A` or `git add .`
- Avoid staging files that may contain secrets (`.env`, credentials, private keys)
- Avoid staging large binaries unless intentional

## Writing the Commit Message

Follow these conventions:

- **First line**: Short imperative summary, ≤72 characters (e.g., "Add user authentication", "Fix null pointer in parser")
- **Body** (optional): Explain *why* — not *what* — if the change needs context. Wrap at 72 characters.
- Match the style of recent commits in this repo (`git log --oneline -10`)

**Verb guide:**
- `Add` — wholly new feature or file
- `Update` — enhancement to existing functionality
- `Fix` — bug fix
- `Remove` — deleting functionality or files
- `Refactor` — restructuring without behavior change

## Creating the Commit

Always pass the message via a heredoc to preserve formatting:

```bash
git commit -m "$(cat <<'EOF'
Short summary here

Optional longer explanation of why this change was made.

EOF
)"
```

## Safety Rules

- NEVER amend published commits unless the user explicitly requests it
- NEVER use `--no-verify` to skip hooks unless explicitly instructed
- NEVER force-push to `main`/`master`
- NEVER commit automatically — only when the user explicitly asks
- If a pre-commit hook fails, fix the underlying issue and create a **new** commit; do NOT amend

## After Committing

Run `git status` to confirm the commit succeeded and the working tree is clean.
