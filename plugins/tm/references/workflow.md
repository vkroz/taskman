# Shared Taskman workflow

Taskman is the source of truth for work shared by Codex, Claude Code, and other agents. The records live in Postgres behind the Taskman server. Use `taskman` (alias `tm`) for every mutation. The server does not read or write the git checkout.

Before editing the repository for an existing item, run `taskman pull ID` and read `.taskman/tasks/ID.md`. That file is a working copy. Do not create or edit files under `tasks/` for live work. Older text files there are not imported; copy their text into `taskman new` by hand.

Set `TASKMAN_URL` and `TASKMAN_ACCESS_TOKEN`. `taskman config` shows the resolved URL and whether a token is present.

## Create and plan

```sh
taskman new --type epic --title "Outcome" --body-file requirements.md --json
taskman new --type feat --title "Story" --parent PARENT_ID --body-file story.md --json
taskman new --title "Work" --parent STORY_ID --body-file task.md --json
taskman depend TASK_ID --on DEPENDENCY_ID
```

A `task` is a leaf. `feat` and `epic` can have children. `depend` replaces the dependency list. Repeat `--on` for several dependencies. `--clear` removes them.

New items start in `backlog`. `taskman pull ID` moves a backlog item to `active` and writes the working copy.

## Execute

```sh
taskman ready --json
taskman context ID
taskman pull ID --agent AGENT --session SESSION
taskman update ID --body-file refined.md
taskman record ID --message "Why this approach."
taskman move ID active
```

Read `context` for the parent, children, dependencies, and events. Journal a short note with `record` when the reason matters.

## Commits, pull requests, and completion

Claude Code plugin hooks report these when `.taskman/active` names the task:

- a Bash `git commit` reports the new HEAD
- a Bash `gh pr create` reports the pull request URL printed by the command
- a session stop reports completion when the transcript contains `Taskman completion: ID`

Run `taskman hooks install` once per git checkout. That installs a post-commit hook for hosts without a plugin hook, including Codex. After creating a pull request without the Claude hook, run `taskman report pr --url URL`.

When the work is done, run:

```sh
taskman complete ID --message "What changed and how it was checked." --agent AGENT
```

`complete` removes `.taskman/active` for that task, and the Stop hook skips a task that is already done, so the two steps record one completion. Include this line in the final message so the Stop hook records it if the command did not run:

```
Taskman completion: ID
```

`close` is an alias of `complete`. Completion marks the task `done`. This version does not freeze the row, and it does not require every child to be done first.

## Flight control

`taskman activity --json` and the web page show the latest reported event on each task: agent, session, kind, and detail. The page polls the server. The server does not call agents.
