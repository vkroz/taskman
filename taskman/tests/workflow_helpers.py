"""Prepare explicit completion evidence for command fixtures."""
from typer.testing import CliRunner
from taskman.cli import app


def prepare(tasks_dir, item_id):
    runner = CliRunner()
    env = {"TASKMAN_TASKS_DIR": str(tasks_dir)}
    result = runner.invoke(app, ["finalize", item_id], env=env)
    assert result.exit_code == 0, result.output
    for kind in ("summary", "verification"):
        args = ["record", item_id, "--kind", kind, "--actor", "pytest", "--body-file", "-"]
        if kind == "verification":
            args += ["--result", "pass"]
        result = runner.invoke(app, args, env=env, input="Fixture prepared for lifecycle command test.\n")
        assert result.exit_code == 0, result.output
