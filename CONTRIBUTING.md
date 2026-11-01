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

Set `TASKMAN_TEST_DATABASE_URL` to use a Postgres you already have. Otherwise pytest starts a Postgres 16 container. Continuous integration does the same with a Postgres 16 service and does not build the Compose stack.

See the [README](README.md) for how to run the server.
