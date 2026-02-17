from __future__ import annotations

from typing import Annotated

import typer

from mcp_email_server.cli.formatting import console, print_error, print_json, print_success
from mcp_email_server.config import EmailSettings, get_settings, store_settings

accounts_app = typer.Typer(help="Manage email accounts")


@accounts_app.command("list")
def list_accounts(
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
) -> None:
    """List all configured email accounts with masked credentials."""
    settings = get_settings()
    accounts = settings.get_accounts(masked=True)

    if not accounts:
        console.print("No accounts configured.")
        return

    if json_output:
        print_json([account.model_dump() for account in accounts])
        return

    from rich.table import Table

    table = Table(title="Email Accounts")
    table.add_column("Name", style="cyan")
    table.add_column("Email", style="yellow")
    table.add_column("Full Name", style="white")
    table.add_column("IMAP Host", style="green")
    table.add_column("SMTP Host", style="green")

    for account in accounts:
        if isinstance(account, EmailSettings):
            table.add_row(
                account.account_name,
                account.email_address,
                account.full_name,
                f"{account.incoming.host}:{account.incoming.port}",
                f"{account.outgoing.host}:{account.outgoing.port}",
            )
        else:
            # ProviderSettings
            table.add_row(
                account.account_name,
                "(provider)",
                "",
                "",
                "",
            )

    console.print(table)


@accounts_app.command("add")
def add_account() -> None:
    """Add a new email account interactively."""
    account_name = typer.prompt("Account name")

    settings = get_settings()
    if settings.get_account(account_name):
        print_error(f"Account '{account_name}' already exists.")
        raise typer.Exit(1)

    full_name = typer.prompt("Full name")
    email_address = typer.prompt("Email address")

    console.print("\n[bold]Incoming (IMAP) server:[/bold]")
    imap_host = typer.prompt("  IMAP host")
    imap_port = typer.prompt("  IMAP port", default=993, type=int)
    imap_user = typer.prompt("  IMAP username", default=email_address)
    imap_password = typer.prompt("  IMAP password", hide_input=True)

    console.print("\n[bold]Outgoing (SMTP) server:[/bold]")
    smtp_host = typer.prompt("  SMTP host")
    smtp_port = typer.prompt("  SMTP port", default=465, type=int)
    smtp_user = typer.prompt("  SMTP username", default=email_address)
    smtp_password = typer.prompt("  SMTP password", hide_input=True)

    email_settings = EmailSettings.init(
        account_name=account_name,
        full_name=full_name,
        email_address=email_address,
        user_name=email_address,
        password=imap_password,
        imap_host=imap_host,
        imap_port=imap_port,
        imap_user_name=imap_user,
        imap_password=imap_password,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user_name=smtp_user,
        smtp_password=smtp_password,
    )

    settings.add_email(email_settings)
    store_settings(settings)
    print_success(f"Account '{account_name}' added successfully.")


@accounts_app.command("remove")
def remove_account(
    name: Annotated[str, typer.Argument(help="Account name to remove")],
) -> None:
    """Remove an email account by name."""
    settings = get_settings()

    if not settings.get_account(name):
        print_error(f"Account '{name}' not found.")
        raise typer.Exit(1)

    typer.confirm(f"Are you sure you want to remove account '{name}'?", abort=True)

    settings.delete_email(name)
    store_settings(settings)
    print_success(f"Account '{name}' removed successfully.")
