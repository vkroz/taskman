# Taskman

Taskman keeps requirements, plans, dependencies, decisions and verification as shared work items in a SQL database. Codex and Claude Code use the same CLI and records, so one agent can implement a task and another can review it without a conversation handoff.

The browser UI creates tasks and displays their hierarchy, status and evidence. Agents plan and execute through the CLI. Completion means the agreed acceptance criteria work locally; deployment is outside this workflow.

## Install

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and the coding tools you want to use.

```sh
uv tool install 'git+https://github.com/vkroz/taskman.git#subdirectory=taskman'
taskman install --platform codex --platform claude-code
```

Choose either platform or repeat `--platform` for both. `taskman` and `tm` are equivalent commands. Start a fresh coding session after installing the plugin.

For a local checkout under development:

```sh
uv tool install --force --editable ./taskman
taskman install --platform codex --platform claude-code --source "$PWD"
```

The CLI installs one shared plugin through host adapters. `taskman install --list-platforms` lists supported hosts. If an existing marketplace points to another source, the installer reports the conflict rather than replacing it.

## Work on a project

Taskman stores work items in SQLite by default, in one file for all repositories: `~/.local/share/taskman/taskman.db`. Create the database once:

```sh
taskman db upgrade
```

Items belong to projects and have keys such as `TM-42`. A new top-level item names its project with `--project`; Taskman creates the project on first use. A child uses the project of its parent.

```sh
taskman new --type epic --title "Add conversation history" --project TM --body-file requirements.md --json
taskman new --type feat --title "Persist conversations" --parent EPIC_ID --body-file story.md --json
taskman new --title "Save messages" --parent STORY_ID --body-file task.md --json
```

Replace the uppercase placeholders with the keys returned by `new`. New items are drafts. Finalize items once their scope is agreed; finalize their ancestors too before execution.

```sh
taskman finalize TASK_ID
taskman depend REVIEW_ID --on TASK_ID
taskman ready --json
taskman context TASK_ID
taskman move TASK_ID active
```

A story uses type `feat`. Taskman permits other nesting shapes; `task` is always a leaf. Independent tasks can run in parallel. Each agent reads `context` and records its contribution through the CLI. Taskman does not launch coding agents itself.

## Record work and verify

```sh
taskman comment TASK_ID --kind decision --actor Codex --body-file decision.md
taskman update TASK_ID --body-file refined-requirements.md
taskman comment TASK_ID --kind summary --actor Codex --body-file summary.md
taskman comment TASK_ID --kind verification --result pass --actor "Claude Code" --body-file verification.md
taskman close TASK_ID
```

`record` is an alias of `comment`. Every command that changes an item accepts `--actor NAME`; without it, the author is your git `user.name`. Use `--body-file -` to read stdin. Comment on significant choices, their reasons and changes to scope. The completion summary should explain what changed, why, and any limits. Verification comments should name the checks, commands, results and source revision or relevant working-tree changes. Distinguish executed tests from source inspection and another agent's report.

Open requirements may be refined to achieve the agreed outcome. Changes to the description, dependencies, type or children, and new decision or progress comments, invalidate earlier completion evidence. Close requires a current summary, a current passing verification, and completed dependencies and descendants. A recorded pass is the agent's assertion: Taskman does not run tests or prove that assertion.

Closed tasks are immutable. Further changes belong in a new task that references the original key. Comments cannot change or be deleted; summaries supplement them. `discard` removes only an unreferenced draft leaf.

## View and check

```sh
taskman ui
taskman list --recursive --json
taskman context TASK_ID
taskman validate
```

The UI refreshes as agents change tasks. `taskman validate` checks the dependency graph for unknown dependencies and cycles; acceptance testing is separate. Use `taskman help` for storage rules and `taskman COMMAND --help` for syntax.

The plugin supplies define, design, plan, implement and validate skills, plus optional review, commenting, commit and documentation skills. Skills use the same task records on either platform.

## Storage settings

Taskman reads its storage settings from environment variables and from a `.env` file at the repository root. A variable already set in the environment wins over `.env`. Docker Compose (`env_file`) and Cloud Run (environment variables and mounted secrets) set the same variables. Taskman ignores a `.env` file that git tracks, because a committed file could send your database password to another host. Keep `.env` out of git.

| Variable | Backend | Meaning |
| --- | --- | --- |
| `TASKMAN_BACKEND` | all | `sqlite` (default) or `postgres`. |
| `TASKMAN_DATABASE_URL` | sqlite | `sqlite:///absolute/path.db` or `sqlite://~/path.db`. Default: `~/.local/share/taskman/taskman.db`, one file for all repositories. |
| `TASKMAN_DATABASE_URL` | postgres | `postgresql://user@host:port/dbname`. |
| `TASKMAN_DB_SOCKET`, `TASKMAN_DB_NAME`, `TASKMAN_DB_USER` | postgres | Use these instead of a URL to connect through a unix socket, for example Cloud SQL. All three are required. |
| `TASKMAN_DB_PASSWORD` | postgres | The database password. Optional. |
| `TASKMAN_DB_PASSWORD_FILE` | postgres | A file that holds the password, for example a mounted secret. Surrounding whitespace is removed. Set this or `TASKMAN_DB_PASSWORD`, not both. |

Error messages never show a password or a connection URL.

`list`, `ready`, `search` and `browse` show all projects; `--project KEY` limits them to one. Items have assignees, labels and an activity history of every field change:

```sh
taskman assign TM-42 claude-code
taskman unassign TM-42
taskman label TM-42 --add backend --remove ui
taskman list --assignee claude-code --label backend
taskman activity TM-42
```

`context` includes the activity of the item. A done item keeps its position among its siblings.

`taskman db upgrade` creates the schema in a new database and upgrades an older one. It does nothing when the schema is current. The postgres backend needs the `postgres` extra: `uv tool install 'taskman[postgres] @ git+https://github.com/vkroz/taskman.git#subdirectory=taskman'`.

SQLite:

```sh
TASKMAN_BACKEND=sqlite
```

Local Postgres:

```sh
TASKMAN_BACKEND=postgres
TASKMAN_DATABASE_URL=postgresql://taskman@localhost:5432/taskman
TASKMAN_DB_PASSWORD=change-me
```

Cloud SQL through its unix socket, for example on Cloud Run:

```sh
TASKMAN_BACKEND=postgres
TASKMAN_DB_SOCKET=/cloudsql/project:region:instance
TASKMAN_DB_NAME=taskman
TASKMAN_DB_USER=taskman_app
TASKMAN_DB_PASSWORD_FILE=/secrets/taskman-db-password
```

## Develop Taskman

```sh
cd taskman
uv sync --extra dev
uv run pytest
```

Without a test database, the Postgres tests are reported as skipped. To run them, start Postgres 16 and point `TASKMAN_TEST_DATABASE_URL` at a database whose name ends in `_test`. The tests delete and recreate its `public` schema. Use a glibc image such as `postgres:16`: Alpine images sort text by bytes and hide collation errors.

```sh
docker run -d --name taskman-pg-test -e POSTGRES_USER=taskman -e POSTGRES_PASSWORD=taskman -e POSTGRES_DB=taskman_test -p 127.0.0.1:54329:5432 postgres:16
uv sync --extra dev --extra postgres
TASKMAN_TEST_DATABASE_URL=postgresql://taskman:taskman@127.0.0.1:54329/taskman_test uv run pytest
```

Python code is under `taskman/src/taskman/`; plugin sources are under `plugins/tm/`. Add a platform adapter to the installer to support another coding tool without changing task storage.

### Workspace UI

Run `taskman ui` for the local workspace. The left navigation selects all, current, backlog or done work. Search combines with epic scope and metadata filters; matching descendants retain labeled parent context. Sort orders siblings; drag an open item to change its position. Expand or collapse branches, and select a title to open details; Close returns the space to the list.

Create work with **New task**; a top-level item needs a project key. Open task details offer title/description editing with a recorded reason, **Add comment** with a kind (and a result for a verification), and **Assignee and labels**. Details also show comments and the activity history. **Delete draft** is limited to draft leaves with no incoming dependencies; finalized and completed work cannot be deleted. Agents finalize, move and close items through the CLI.

The same operations are available from the CLI:

```sh
taskman browse --where status=active,backlog --sort title
taskman browse --where status=done --epic EPIC_KEY --keyword migration
taskman browse --contains journal=verification --exists depends_on
taskman edit TASK_KEY --title "Updated title" --body-file requirements.md --reason "Clarify table coverage"
taskman reorder TASK_KEY --position 1
taskman discard DRAFT_KEY
```

`browse` emits hierarchy-preserving JSON; contextual ancestors have `context: true` and do not match the query themselves. Use `show` or `context` with a key for the complete record. See [workspace design](docs/ui-design.md) for filtering semantics. In JSON, comments are in `frontmatter.journal`.
