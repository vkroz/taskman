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

Taskman reads its storage settings from environment variables and from a `.env` file at the repository root. A variable already set in the environment wins over `.env`. Docker Compose and Cloud Run set the same variables, as environment variables and mounted secret files. Taskman ignores a `.env` file that git tracks, because a committed file could send your database password to another host. Keep `.env` out of git.

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
TASKMAN_DB_USER=taskman
TASKMAN_DB_PASSWORD_FILE=/secrets/db/password
```

## Use a Taskman server

A Taskman server lets agents and people on different computers share the same work items. The server alone opens the database. The CLI on each computer calls the server and does not know which database it uses.

On a machine that reaches the database, with the storage settings above:

```bash
taskman db upgrade                          # create or upgrade the schema
taskman token create --person "Ada"         # prints a token one time; it expires after 90 days
taskman serve                               # listens on 127.0.0.1 and the port in PORT, or 8080
```

`taskman serve --host 0.0.0.0` accepts calls from other machines. `taskman token list` shows the tokens without their secrets, and `taskman token revoke ID` stops one.

### Run the server in a container

`taskman/` holds a `Dockerfile` and a Docker Compose file. The Compose stack runs Postgres 16, the schema upgrade and the server. The server connects to Postgres through a unix socket, as on Cloud Run. On a computer with Docker, for example a MacBook:

```sh
cd taskman
mkdir -p .secrets && python3 -c "import secrets; print(secrets.token_urlsafe(24))" > .secrets/db_password
docker compose up -d --build
docker compose run --rm server token create --person "you@example.com"
export TASKMAN_SERVER_URL=http://127.0.0.1:8080 TASKMAN_TOKEN=<printed token>
```

Replace `<printed token>` with the token that `token create` printed. Git ignores `taskman/.secrets/`. The server accepts calls only from this computer, on `127.0.0.1:8080`. After you get a new version, run `docker compose up -d --build` again: the stack upgrades the schema before the server starts. `docker compose down` stops the stack and keeps the data.

On Linux with umask 077, the container user cannot read the password file: run `chmod 644 .secrets/db_password`. The server does not connect again after Postgres restarts: run `docker compose restart server`. To change the password, change it in Postgres or delete all local data with `docker compose down -v`: Postgres reads the file only when it creates its data volume.

To run the same image on Google Cloud Run with Cloud SQL, follow [docs/deploy-gcp.md](docs/deploy-gcp.md). It describes the GitHub Actions workflow that deploys the `staging` branch, and the steps to deploy by hand.

### Log in

On a computer with a browser:

```bash
taskman auth login --server https://taskman.example.com
```

The command opens a browser. You sign in with your Google account, and the CLI saves its own token for this computer. Every later command works through the server. `taskman auth status` shows the server and your email address. `taskman auth logout` revokes the token and removes it.

On a machine without a browser, for example a cloud virtual machine:

1. On any computer, open `https://taskman.example.com/auth/token` and sign in. The page shows a personal access token one time.
2. On the machine, run `taskman auth login --server https://taskman.example.com --with-token` and paste the token.

An agent sandbox that accepts only environment variables can set `TASKMAN_SERVER_URL` and `TASKMAN_TOKEN` instead. `TASKMAN_TOKEN` wins over a saved login.

The person of the token is the author of each change. With `--actor claude`, the author is `claude for ada@example.com`. Taskman reads `TASKMAN_SERVER_URL` and `TASKMAN_TOKEN` from the environment only, never from a `.env` file. The address must start with `https://`, unless the server runs on the same computer. The CLI keeps its login in `~/.config/taskman/credentials.json`, which only you can read. A token expires after 90 days.

### Turn on browser login

Browser login needs a Google OAuth (Open Authorization) client of type "Web application". Create it on the Clients page of Google Auth Platform in the Google Cloud console, as [Turn on browser login](docs/deploy-gcp.md#turn-on-browser-login) shows. Add `<public address>/auth/callback` as an authorized redirect URI (Uniform Resource Identifier). Then start the server with:

| Variable | Value |
| --- | --- |
| `TASKMAN_GOOGLE_CLIENT_ID` | The client ID. Browser login is off without it. |
| `TASKMAN_GOOGLE_CLIENT_SECRET` or `TASKMAN_GOOGLE_CLIENT_SECRET_FILE` | The client secret, or a file that holds it. |
| `TASKMAN_PUBLIC_URL` | The address that people open, for example `https://taskman.example.com`. |
| `TASKMAN_ALLOWED_EMAILS` | Comma-separated email addresses that may sign in. |
| `TASKMAN_ALLOWED_DOMAINS` | Comma-separated Google Workspace domains. Every member of these organizations may sign in. Google must report the account as managed by the organization; an address in the domain alone is not enough. Set this, `TASKMAN_ALLOWED_EMAILS`, or both. |

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

The browser tests of the workspace page need the `ui` extra, which installs Playwright 1.63.0. They also need its Chromium build (`uv run playwright install chromium`). Without Playwright or Chromium, pytest reports them as skipped. `uv run pytest -m ui` runs only the workspace page tests.

```sh
uv sync --extra dev --extra postgres --extra ui
uv run pytest -m ui
```

Python code is under `taskman/src/taskman/`; plugin sources are under `plugins/tm/`. Add a platform adapter to the installer to support another coding tool without changing task storage.

### Workspace UI

Run `taskman ui` for the local workspace. The sidebar selects a view (Attention, Active, Ready, Blocked, Drafts, Backlog or Done) and a project. Search, facet filters and the epic scope apply at once and stay in the URL. Sort orders siblings; drag an open item to change its position within its parent and project. Select a row to open its details; Back or Esc closes them. [docs/ui-design.md](docs/ui-design.md) describes the page.

Create work with **New**; a top-level item needs a project key. Details show the state, blockers, close readiness and one timeline of comments and changes. **Edit** changes the title and description with a recorded reason. The menu offers **Add comment** with a kind (and a result for a verification), **Assignee and labels**, and **Delete draft** for draft leaves with no incoming dependencies. Finalized and completed work cannot be deleted. Agents finalize, move and close items through the CLI.

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
