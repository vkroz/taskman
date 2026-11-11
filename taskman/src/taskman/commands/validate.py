"""`validate` command — integrity check across the work items.

Reports dependency findings: unknown dependency keys and dependency cycles.
Each finding has a ``severity`` (``error`` or ``warning``). The command exits non-zero if any error finding is present.
"""
from __future__ import annotations

import json
from dataclasses import asdict

import typer

from taskman.integrity import check
from taskman.storage.store import open_store


def validate(
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Check the dependency graph of all work items."""
    findings = check(open_store())
    has_error = any(f.severity == "error" for f in findings)

    if as_json:
        typer.echo(
            json.dumps(
                {"findings": [asdict(f) for f in findings], "ok": not has_error},
                indent=2,
            )
        )
    else:
        if not findings:
            typer.echo("OK")
        else:
            for f in findings:
                typer.echo(f"[{f.severity.upper()}] {f.code}: {f.path}")
                typer.echo(f"    {f.message}")
            summary = f"{len(findings)} finding(s); errors: " + str(
                sum(1 for f in findings if f.severity == "error")
            )
            typer.echo(summary)

    if has_error:
        raise typer.Exit(1)
