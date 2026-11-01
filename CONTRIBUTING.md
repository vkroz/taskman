# Contributing

## Branches

`main` is protected. Do not push commits to it.

Create work on one of these branches:

- `feat/<short-name>` for a product change
- `chore/<short-name>` for maintenance

Do not use a `cursor/` prefix.

The path into `main` is:

1. Open a pull request from the `feat/*` or `chore/*` branch.
2. Land that work on `staging`. Automated tests run on pull requests into `staging` and `main`, and on every push to `staging`.
3. Promote the result to `main` with a pull request. That promotion is the only way a change reaches `main`.

`staging` was created from `main` and is the branch those automated tests use.

## Tests

From `taskman/`:

```sh
uv sync --extra dev --extra serve
uv run pytest
```

**Warning:** the tests TRUNCATE every Taskman table. Never point them at a database that holds real data. Pytest refuses to run unless the database name ends in `_test`.

Set `TASKMAN_TEST_DATABASE_URL` to a disposable database, for example `postgresql://user:pass@host:5432/taskman_test`. Otherwise pytest starts a Postgres 16 container named `taskman-pytest-pg-test` on port 54329. That container keeps running after the tests; remove it with `docker rm -f taskman-pytest-pg-test`.

When `TASKMAN_TEST_DATABASE_URL` or `CI` is set and Postgres is unreachable, the database tests fail. Without either variable, they skip when Docker is missing or the container cannot start.

Continuous integration runs the tests against a Postgres 16 service and does not build the Compose stack.

See the [README](README.md) for how to run the server.
