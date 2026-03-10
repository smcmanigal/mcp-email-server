from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

import typer

from mcp_email_server.cli.formatting import (
    console,
    print_error,
    print_json,
    print_rules_results,
    print_rules_table,
    print_success,
)

rules_app = typer.Typer(help="Email filter rules")


@rules_app.command("list")
def list_rules(
    account: Annotated[str | None, typer.Option("--account", "-a", help="Filter by account name")] = None,
    file: Annotated[str | None, typer.Option("--file", "-f", help="Filter by rule file name")] = None,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
) -> None:
    """List all filter rules."""
    from mcp_email_server.rules import RULES_DIR, load_all_rules

    try:
        rules_by_file = load_all_rules(account=account, file_name=file)
        if json_output:
            data = {f: [r.model_dump() for r in rules] for f, rules in rules_by_file.items()}
            print_json(data)
        else:
            print_rules_table(rules_by_file, RULES_DIR)
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("apply")
def apply_rules_cmd(
    account: Annotated[str | None, typer.Option("--account", "-a", help="Filter by account name")] = None,
    file: Annotated[str | None, typer.Option("--file", "-f", help="Filter by rule file name")] = None,
    since: Annotated[datetime | None, typer.Option(help="Only match emails since datetime")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show matches without moving")] = False,
    limit: Annotated[int | None, typer.Option("--limit", "-l", help="Max emails to process per rule", min=1)] = None,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
) -> None:
    """Apply filter rules to move matching emails."""
    from mcp_email_server.rules import apply_rules, load_all_rules

    try:
        rules_by_file = load_all_rules(account=account, file_name=file)
        if not rules_by_file:
            console.print("[dim]No rules to apply.[/dim]")
            return
        results = asyncio.run(apply_rules(rules_by_file, since=since, dry_run=dry_run, limit=limit))
        if json_output:
            print_json([r.model_dump() for r in results])
        else:
            if dry_run:
                console.print("[yellow]Dry run mode — no emails were moved.[/yellow]")
            print_rules_results(results)
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("add")
def add_rule_cmd(
    file: Annotated[str, typer.Option("--file", "-f", help="Rule file name (e.g. ads.toml)")],
    name: Annotated[str, typer.Option("--name", "-n", help="Rule name")],
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    target_folder: Annotated[str, typer.Option("--target-folder", "-t", help="Target folder for matched emails")],
    senders: Annotated[str | None, typer.Option("--senders", "-s", help="Comma-separated sender substrings")] = None,
    subjects: Annotated[str | None, typer.Option("--subjects", help="Comma-separated subject substrings")] = None,
    source_mailbox: Annotated[str, typer.Option("--source-mailbox", help="Source mailbox")] = "INBOX",
    mark_read: Annotated[bool, typer.Option("--mark-read", help="Mark emails as read before moving")] = False,
) -> None:
    """Add a new filter rule. Specify either --senders or --subjects (not both)."""
    from mcp_email_server.rules import Rule, add_rule

    try:
        if senders and subjects:
            print_error("Specify either --senders or --subjects, not both.")
            raise typer.Exit(1)
        if not senders and not subjects:
            print_error("Either --senders or --subjects is required.")
            raise typer.Exit(1)

        sender_list = [s.strip() for s in senders.split(",") if s.strip()] if senders else []
        subject_list = [s.strip() for s in subjects.split(",") if s.strip()] if subjects else []

        if senders and not sender_list:
            print_error("At least one sender is required.")
            raise typer.Exit(1)
        if subjects and not subject_list:
            print_error("At least one subject is required.")
            raise typer.Exit(1)

        rule = Rule(
            name=name,
            account=account,
            target_folder=target_folder,
            senders=sender_list,
            subjects=subject_list,
            source_mailbox=source_mailbox,
            mark_read=mark_read,
        )
        add_rule(file, rule)
        print_success(f"Added rule '{name}' to {file}")
    except typer.Exit:
        raise
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("delete")
def delete_rule_cmd(
    file: Annotated[str, typer.Option("--file", "-f", help="Rule file name")],
    name: Annotated[str, typer.Option("--name", "-n", help="Rule name to delete")],
) -> None:
    """Delete a filter rule by name."""
    from mcp_email_server.rules import delete_rule

    try:
        deleted = delete_rule(file, name)
        if deleted:
            print_success(f"Deleted rule '{name}' from {file}")
        else:
            print_error(f"Rule '{name}' not found in {file}")
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)
