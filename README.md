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
| `taskman` | Image `taskman:local`. Command `taskman serve`. gunicorn on `0.0.0.0:$PORT`. |

`http://127.0.0.1:8080/` is the flight-control page. Paste the token into the page; it stays in the browser tab and is sent as `Authorization: Bearer`. `GET /health` is open and returns `{"status":"ok"}` when Postgres answers.

```sh
docker compose ps
docker compose logs -f taskman
docker compose restart
docker compose down
```

`docker compose down` keeps the database volume. `docker compose down -v` deletes it.

The app waits for Postgres, then creates tables if they are missing. Nothing in this repository is specific to a cloud vendor. The same Compose file is the application. A later deploy swaps only the outer hosting config.

### Environment

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TASKMAN_ACCESS_TOKEN` | yes for the server | | Shared bearer token. Not committed. |
| `PORT` | no | `8080` | Published host port and the port inside the app container. |
| `TASKMAN_DATABASE_URL` | set by Compose | `postgresql://taskman:taskman@db:5432/taskman` | Postgres URL the app uses. |
| `POSTGRES_USER` | no | `taskman` | Database user. Use a URL-safe password. |
| `POSTGRES_PASSWORD` | no | `taskman` | Database password for local Compose. |
| `POSTGRES_DB` | no | `taskman` | Database name. |
| `TASKMAN_WEB_WORKERS` | no | `2` | gunicorn workers. |
| `TASKMAN_URL` | CLI | `http://127.0.0.1:8080` | Server the CLI calls. |
| `TASKMAN_AGENT` | no | | Name stored on events from this process. |
| `TASKMAN_SESSION` | no | | Session id stored on events. |

`taskman config --server http://127.0.0.1:8080` saves the URL in `~/.config/taskman/config.json`. The token should stay in the environment. A config file may also contain `"token"`, but do not commit that file.

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
taskman record ID --message "Use the existing parser."
taskman complete ID --message "Export writes the file."
taskman activity --json
taskman validate
```

`close` is an alias of `complete`. `taskman pull` writes `.taskman/tasks/<id>.md` and `.taskman/active` in the current directory, and marks a backlog task active. That file is the working copy agents read. The server does not create it.

`taskman ui` prints the server URL. It does not start a second server.

### Hooks

Claude Code loads `plugins/tm/hooks/hooks.json` with the plugin. After a Bash `git commit` or `gh pr create`, and when a session stops with the line `Taskman completion: ID` in the transcript, the hook runs `taskman hook claude`. The active task id comes from `.taskman/active`.

Codex does not get a plugin hook from this repository. In a git checkout, run:

```sh
taskman hooks install
```

That writes `.git/hooks/post-commit`, which runs `taskman hook git commit`. Report a pull request with:

```sh
taskman report pr --url https://github.com/org/repo/pull/12
```

Hooks exit 0 when the server is unreachable so a commit is not rejected. Set `TASKMAN_URL`, `TASKMAN_ACCESS_TOKEN`, and optionally `TASKMAN_AGENT` where the hook runs.

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
- Several gunicorn workers on one machine share Postgres. Run one app service against the database.

## Develop

```sh
cd taskman
uv sync --extra dev --extra serve
uv run pytest
```

Pytest uses `TASKMAN_TEST_DATABASE_URL` when it is set. Otherwise it starts a Postgres 16 container named `taskman-pytest-pg` on port 54329. Plugin sources live under `plugins/tm/`. Edit those files, not the `.claude/` symlinks.
