# Taskman

Taskman is the shared record of work for coding agents. The records live in Postgres. A small web server is the only writer. The `taskman` CLI is an HTTP client, and the flight-control page shows which agent last reported activity on each task.

The server does not read or write a git checkout. An agent copies a task into the checkout with `taskman pull`. Hooks report commits, pull requests, and completion back to the server.

## Run

Requires Docker.

```sh
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put that value in `.env` as `TASKMAN_ACCESS_TOKEN`. Then, from the repository root:

```sh
docker compose up -d --build
```

The stack is two services:

| Service | Role |
| --- | --- |
| `db` | Postgres 16. Named volume `taskman-db` mounted at `/var/lib/postgresql/data`. |
| `taskman` | Image `taskman:local`. Command `taskman serve`. gunicorn on `0.0.0.0:$PORT` inside the container. Compose publishes that port on `127.0.0.1` only. |

`http://127.0.0.1:8080/` is the flight-control page. Paste the token into the page; it stays in the browser tab and is sent as `Authorization: Bearer`. `GET /health` is liveness: it does not open a database connection and returns `{"status":"ok"}` when the process is up. `GET /ready` is readiness: it runs `SELECT 1` and returns `{"status":"unavailable"}` with status 503 when Postgres does not answer. Neither check requires the token. The image and Compose healthchecks call `/ready`.

```sh
docker compose ps
docker compose logs -f taskman
docker compose restart
docker compose down
```

`docker compose down` keeps the database volume. `docker compose down -v` deletes it.

The app waits for Postgres, then creates tables if they are missing. `taskman migrate` does only that step and exits, so a deploy can migrate before it starts the new process. Docker Compose stays the local setup and does not mention a cloud vendor. A push to `staging` or `main` deploys this same image to Cloud Run. That pipeline is described in [docs/deploy.md](docs/deploy.md).

### Environment

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TASKMAN_ACCESS_TOKEN` | yes for the server | | Shared bearer token. Not committed. |
| `PORT` | no | `8080` | Published host port and the port inside the app container. |
| `TASKMAN_DATABASE_URL` | set by Compose | `postgresql://taskman:taskman@db:5432/taskman` | Postgres connection string. When this is set, the socket variables below are ignored. |
| `POSTGRES_USER` | no | `taskman` | Database user. Use a URL-safe password. |
| `POSTGRES_PASSWORD` | no | `taskman` | Database password for local Compose. |
| `POSTGRES_DB` | no | `taskman` | Database name. |
| `TASKMAN_DB_SOCKET` | cloud only | | Directory of the Cloud SQL unix socket, for example `/cloudsql/project:region:instance`. Used only when `TASKMAN_DATABASE_URL` is unset. |
| `TASKMAN_DB_NAME` | cloud only | | Database name for that socket connection. |
| `TASKMAN_DB_USER` | cloud only | | Database user for that socket connection. |
| `TASKMAN_DB_PASSWORD` | cloud only | | Database password. Surrounding whitespace is removed. Secret Manager mounts this on Cloud Run. |
| `TASKMAN_WEB_WORKERS` | no | `2` | gunicorn workers. |
| `TASKMAN_URL` | CLI | `http://127.0.0.1:8080` | Server the CLI calls. |
| `TASKMAN_SESSION` | no | | Session id stored on events. |

`taskman config --server http://127.0.0.1:8080` saves the URL in `~/.config/taskman/config.json` (mode `0600`). The CLI reads the server URL and token only from that user file and from the environment. A `.taskman/config.json` inside a repository is ignored, so a checkout cannot point the token at another host.

The Compose database password defaults to `taskman`. Change `POSTGRES_PASSWORD` in `.env` before anyone else can reach the machine. Keep the value URL-safe; Compose embeds it in `TASKMAN_DATABASE_URL`.

`taskman install` exits 0 when a requested host CLI is missing, including when every host is skipped, and prints a warning with the command to finish. That keeps `install.sh` from failing after the CLI itself is installed. It exits 2 when a platform name or source is invalid, or when a host CLI is present and its plugin command fails.

### CLI

```sh
export TASKMAN_URL=http://127.0.0.1:8080
export TASKMAN_ACCESS_TOKEN='the-token-from-env'
taskman new --title "Add export" --type task --body-file requirements.md --json
taskman list --json
taskman show ID
taskman update ID --body-file requirements.md
taskman move ID active
taskman depend ID --on OTHER
taskman ready --json
taskman context ID
taskman pull ID --agent codex --session local
taskman record ID --message "Use the existing parser." --agent codex
taskman complete ID --message "Export writes the file." --agent codex
taskman activity --json
taskman validate
```

`--agent` (alias `--author`) names the **author** of the changes an event records: a person, `claude-code`, `codex`, and so on. Taskman does not control agents. Every command that records an event requires it: `pull`, `record`, `complete`, `close`, `report commit|pr|completion`, and `hook claude|git`. The value comes only from the command line; no environment variable or default fills it in. A missing or blank value fails with `--agent <author> is required: name who authored these changes`. Surrounding whitespace is trimmed. The server also rejects a blank author.

`close` is an alias of `complete`. `taskman pull` writes `.taskman/tasks/<id>.md` and `.taskman/active` at the git repository root, adds `.taskman/` to that repository's `.gitignore` when it is missing, and marks a backlog task active. That file is the working copy agents read. The server does not create it. `taskman complete` removes `.taskman/active` when it names the completed task.

`taskman ui` prints the server URL. It does not start a second server.

### Hooks

Claude Code loads `plugins/tm/hooks/hooks.json` with the plugin. After a Bash `git commit` or `gh pr create`, and when a session stops with the line `Taskman completion: ID` in the last assistant message, the hook runs `taskman hook claude --agent claude-code`, so every event it reports records `claude-code` as the author. The active task id comes from `.taskman/active`. The Stop hook skips a task that is already done, so `taskman complete` plus the marker line records one completion. The hook script limits the client to 4 seconds with `timeout`, or `gtimeout` on macOS with Homebrew coreutils. With neither, it runs the client unwrapped, and the client's 3-second network timeout applies.

Codex does not get a plugin hook from this repository. In a git checkout, run:

```sh
taskman hooks install
```

That writes `.git/hooks/post-commit`, which runs `taskman hook git commit --agent "$author"`. The author is the commit author from `git log -1 --format=%an`, then `git config user.name`, then the literal `git`. A post-commit hook from an older Taskman runs without `--agent`, so it reports nothing and prints how to update it. `taskman hooks install` replaces an unmodified older Taskman hook and keeps a backup; a hook you edited needs `--force`. Report a pull request with:

```sh
taskman report pr --url https://github.com/org/repo/pull/12 --agent codex
```

Hooks exit 0 when the server is unreachable so a commit is not rejected. Set `TASKMAN_URL` and `TASKMAN_ACCESS_TOKEN` where the hook runs.

### Install the CLI and plugins

```sh
uv tool install 'git+https://github.com/vkroz/taskman.git#subdirectory=taskman'
taskman install --platform codex --platform claude-code --server http://127.0.0.1:8080
```

From a working tree:

```sh
uv tool install --force --editable ./taskman
taskman install --platform claude-code --source "$PWD" --server http://127.0.0.1:8080
```

`taskman install` registers each requested platform whose host CLI (`codex` or `claude`) is on `PATH`. A missing host CLI is skipped, with the exact command to run after you install it, and the process still exits 0. The command exits 2 when a platform name or `--source` is invalid, or when a host CLI is present but its plugin command fails.

### Existing task files

Text files under `tasks/` in a repository are not imported. Copy the title and body into `taskman new` yourself. The server never scans a repo for them.

### Limits of this version

- One shared token. No accounts, no session table, no rate limit. The comparison is constant-time. The service speaks plain HTTP, so the token is visible to anything that can read the traffic.
- The page shell at `/` contains no task data. Task data requires the bearer token.
- The dashboard shows the latest event on each task. It updates because the browser polls. The server does not call out to agents.
- There is no delete, no draft/finalize step, and no rule that a parent stays open until every child is done. Completion is `taskman complete` or the Stop hook line above.
- A pull event is recorded when an agent runs `taskman pull`, so the board can show who claimed the task before the first commit. The three hook events are commit, pull request, and completion.
- Posting an event that is already stored returns 200 and that event. This covers a commit whose sha is already recorded for the task, and a completion that repeats the task's latest event when that event is a completion for the same agent and session. A post that inserts a row returns 201. The duplicate answer is 200 rather than 409 so a retry stays successful for the CLI and the hooks.
- Several gunicorn workers on one machine share Postgres. Run one app service against the database.

## Develop

```sh
cd taskman
uv sync --extra dev --extra serve
uv run pytest
```

Pytest uses `TASKMAN_TEST_DATABASE_URL` when it is set. Otherwise it starts a Postgres 16 container named `taskman-pytest-pg-test` on port 54329. The tests TRUNCATE every Taskman table, so they refuse a database whose name does not end in `_test`. See [CONTRIBUTING.md](CONTRIBUTING.md). Plugin sources live under `plugins/tm/`. Edit those files, not the `.claude/` symlinks.

Branch names are `feat/*` or `chore/*`. Do not push to `main`. The path into `main` is described in [CONTRIBUTING.md](CONTRIBUTING.md).
