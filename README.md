# Taskman

Taskman keeps requirements, plans, dependencies, decisions and verification in a shared task tree. Codex and Claude Code use the same CLI and records, so one agent can implement a task and another can review it without a conversation handoff.

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

Run Taskman inside the project. It finds the nearest ancestor `tasks/` directory, stopping at the repository root. Set `TASKMAN_TASKS_DIR` explicitly when multiple agents must share a particular tree across working directories.

```sh
taskman new --type epic --title "Add conversation history" --body-file requirements.md --json
taskman new --type feat --title "Persist conversations" --parent EPIC_ID --body-file story.md --json
taskman new --title "Save messages" --parent STORY_ID --body-file task.md --json
```

Replace the uppercase placeholders with the IDs returned by `new`. New items are drafts. Finalize items once their scope is agreed; finalize their ancestors too before execution.

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
taskman record TASK_ID --kind decision --actor Codex --body-file decision.md
taskman update TASK_ID --body-file refined-requirements.md
taskman record TASK_ID --kind summary --actor Codex --body-file summary.md
taskman record TASK_ID --kind verification --result pass --actor "Claude Code" --body-file verification.md
taskman close TASK_ID
```

Use `--body-file -` to read stdin. Journal significant choices, their reasons and changes to scope. The completion summary should explain what changed, why, and any limits. Verification records should name the checks, commands, results and source revision or relevant working-tree changes. Distinguish executed tests from source inspection and another agent's report.

Open requirements may be refined to achieve the agreed outcome. Changes to the body, dependencies, type, implementation journal or children invalidate earlier completion evidence. Close requires a current summary, a current passing verification, and completed dependencies and descendants. A recorded pass is the agent's assertion: Taskman does not run tests or prove that assertion.

Closed tasks are immutable. Further changes belong in a new task that references the original ID. Summaries supplement the journal; original entries remain available. `discard` removes only an unreferenced draft leaf.

## View and check

```sh
taskman ui
taskman list --recursive --json
taskman context TASK_ID
taskman validate
```

The UI refreshes as agents change tasks. `taskman validate` checks task-data integrity; acceptance testing is separate. Use `taskman help` for storage rules and `taskman COMMAND --help` for syntax.

The plugin supplies define, design, plan, implement and validate skills, plus optional review, commenting, commit and documentation skills. Skills use the same task records on either platform.

## Develop Taskman

```sh
cd taskman
uv sync --extra dev
uv run pytest
```

Python code is under `taskman/src/taskman/`; plugin sources are under `plugins/tm/`. Add a platform adapter to the installer to support another coding tool without changing task storage. The shell script under `plugins/tm/scripts/` is retained for legacy data; current workflows use the Python CLI.

### Workspace UI and history

Run `taskman ui` for the local workspace. The left navigation selects current, backlog, done or consolidated history. Search combines with epic scope and metadata filters; matching descendants retain labeled parent context. Sort orders siblings. Expand or collapse branches, and select a title to open details; Close returns the space to the list.

Create work with **New task**. Open task details offer title/description editing with a recorded reason. **Delete draft** is limited to draft leaves with no incoming dependencies; finalized and completed work cannot be deleted through this command.

Select completed rows and choose **Consolidate completed work** to create a Markdown documentation summary. Selecting a parent includes its descendants. Originals remain immutable and retrievable in Consolidated history, while individual rows leave ordinary lists. The summary contains bounded excerpts and links to full requirements and evidence.

The same operations are available from the CLI:

```sh
taskman browse --where status=active,backlog --sort title
taskman browse --where status=done --epic EPIC_ID --keyword migration
taskman browse --contains journal=verification --exists depends_on
taskman edit TASK_ID --title "Updated title" --body-file requirements.md --reason "Clarify table coverage"
taskman discard DRAFT_ID
taskman consolidate DONE_ID --title "Delivered database changes"
taskman history
taskman browse --include-archived
```

`browse` emits hierarchy-preserving JSON; contextual ancestors have `context: true` and do not match the query themselves. Use `show` or `context` with any retained ID for complete history. See [workspace design](docs/ui-design.md) for filtering and retention semantics.

## Run with Docker

The container serves the same workspace UI and HTTP API as `taskman ui`, through gunicorn, on `0.0.0.0` and the port in `PORT` (default `8080`). `taskman ui` remains the localhost process. `taskman serve` is the networked process the image starts.

From the repository root:

```sh
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put that value in `.env` as `TASKMAN_ACCESS_TOKEN`. Then build and start the app:

```sh
docker compose up -d --build
```

The service is at `http://127.0.0.1:8080/`. Sign in with the token, or send `Authorization: Bearer <token>` on API requests. `GET /health` is open and returns `{"status":"ok"}` when the task directory is writable.

```sh
docker compose ps
docker compose logs -f taskman
docker compose exec taskman taskman validate
docker compose down
```

`docker compose down` keeps the named volume `taskman-data`. `docker compose down -v` deletes that volume and the tasks stored in it.

### Configuration

| Variable | Required | Purpose |
| --- | --- | --- |
| `TASKMAN_ACCESS_TOKEN` | yes | Shared secret for the login form and `Authorization: Bearer`. |
| `PORT` | no | Published host port and the port inside the container. Default `8080`. |
| `TASKMAN_TASKS_DIR` | set by Compose | Task tree inside the container. Compose sets `/data/tasks`. |
| `TASKMAN_WEB_WORKERS` | no | Gunicorn worker processes. Default `2`. |

Compose passes these in. Copy `.env.example` to `.env` for local values. `.env` is gitignored.

The image command is `taskman serve`. With `TASKMAN_ACCESS_TOKEN` set, it execs gunicorn bound to `0.0.0.0:$PORT`. With the token unset, the process exits. The same command runs on a machine without Compose, from `taskman/` after `uv sync --extra dev --extra serve`:

```sh
TASKMAN_ACCESS_TOKEN='replace-with-a-long-random-token' TASKMAN_TASKS_DIR=/absolute/path/to/tasks uv run taskman serve
```

### Persistence

Task files stay on a Docker volume mounted at `/data`. The task tree is `/data/tasks`. Consolidated history is written next to that tree, at `/data/docs/taskman-history`, so both sit on the volume. Restarting the container, rebuilding the image, and recreating the container keep the files while the volume exists.

The volume stores the same markdown files the CLI already uses, so the container and the CLI share one tree. A shell in the container can run `taskman` against `TASKMAN_TASKS_DIR`. To put the files on a host path, replace the named volume with a bind mount such as `/var/lib/taskman:/data` or `./taskman-data:/data`. The process runs as uid `10001`; that user must be able to write the mounted directory. The volume is local to this Docker host. It survives container restarts and image rebuilds. Removing the volume removes the tasks.

### Access

The token is the access check for every route except `GET /health` and the login page. The login form sets an `HttpOnly` `SameSite=Lax` cookie whose value is an HMAC of the token. Mutating requests also send the `X-Taskman: 1` header the UI already uses. The server speaks plain HTTP, so the token is visible to anything on the path that can read the traffic. Keep a long random value in `.env` or the process environment. One shared secret covers every caller, and login attempts are not rate-limited.
