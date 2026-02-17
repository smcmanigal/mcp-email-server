from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from mcp_email_server.cli.formatting import console, print_error, print_json, print_success
from mcp_email_server.emails.dispatcher import dispatch_handler

flags_app = typer.Typer(help="Flag operations")


def _run_flag_operation(
    operation: str,
    account: str,
    email_ids: list[str],
    flags: list[str],
    mailbox: str,
    silent: bool,
    json_output: bool,
) -> None:
    handler = dispatch_handler(account)
    id_strings = [str(eid) for eid in email_ids]
    method = getattr(handler, operation)
    results = asyncio.run(method(id_strings, flags, silent))
    successful = sum(results.values())
    output = {
        "results": results,
        "total_modified": successful,
        "failed": len(email_ids) - successful,
        "operation": operation,
        "flags": flags,
        "silent": silent,
    }
    if json_output:
        print_json(output)
    else:
        print_success(
            f"{operation}: {successful} email(s) modified, "
            f"{len(email_ids) - successful} failed. "
            f"Flags: {', '.join(flags)}"
        )


@flags_app.command("add")
def add_flags(
    email_ids: Annotated[list[str], typer.Argument(help="Email IDs to modify")],
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    flag: Annotated[list[str], typer.Option("--flag", "-f", help="Flag to add (can be repeated)")],
    mailbox: Annotated[str, typer.Option(help="Mailbox name")] = "INBOX",
    silent: Annotated[bool, typer.Option(help="Suppress server responses")] = False,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
) -> None:
    """Add flags to one or more emails."""
    try:
        _run_flag_operation("add_flags", account, email_ids, flag, mailbox, silent, json_output)
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(code=1) from None


@flags_app.command("remove")
def remove_flags(
    email_ids: Annotated[list[str], typer.Argument(help="Email IDs to modify")],
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    flag: Annotated[list[str], typer.Option("--flag", "-f", help="Flag to remove (can be repeated)")],
    mailbox: Annotated[str, typer.Option(help="Mailbox name")] = "INBOX",
    silent: Annotated[bool, typer.Option(help="Suppress server responses")] = False,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
) -> None:
    """Remove flags from one or more emails."""
    try:
        _run_flag_operation("remove_flags", account, email_ids, flag, mailbox, silent, json_output)
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(code=1) from None


@flags_app.command("replace")
def replace_flags(
    email_ids: Annotated[list[str], typer.Argument(help="Email IDs to modify")],
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    flag: Annotated[list[str], typer.Option("--flag", "-f", help="Flag to set (can be repeated)")],
    mailbox: Annotated[str, typer.Option(help="Mailbox name")] = "INBOX",
    silent: Annotated[bool, typer.Option(help="Suppress server responses")] = False,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
) -> None:
    """Replace all flags on one or more emails."""
    try:
        _run_flag_operation("replace_flags", account, email_ids, flag, mailbox, silent, json_output)
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(code=1) from None
