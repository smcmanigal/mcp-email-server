from __future__ import annotations

from typing import Annotated

import click
import typer

from mcp_email_server.cli.formatting import console, print_error, print_json, print_success
from mcp_email_server.config import EmailServer, EmailSettings, get_settings, store_settings

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
    table.add_column("Auth", style="magenta")

    for account in accounts:
        if isinstance(account, EmailSettings):
            auth_info = account.auth_type
            if account.auth_type == "oauth2" and account.oauth2_provider:
                auth_info = f"oauth2 ({account.oauth2_provider})"
            table.add_row(
                account.account_name,
                account.email_address,
                account.full_name,
                f"{account.incoming.host}:{account.incoming.port}",
                f"{account.outgoing.host}:{account.outgoing.port}",
                auth_info,
            )
        else:
            # ProviderSettings
            table.add_row(
                account.account_name,
                "(provider)",
                "",
                "",
                "",
                "provider",
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


@accounts_app.command("add-oauth2")
def add_oauth2_account() -> None:
    """Add a new email account with OAuth2 authentication (Microsoft 365 or Google)."""
    account_name = typer.prompt("Account name")

    settings = get_settings()
    if settings.get_account(account_name):
        print_error(f"Account '{account_name}' already exists.")
        raise typer.Exit(1)

    full_name = typer.prompt("Full name")
    email_address = typer.prompt("Email address")

    provider = typer.prompt("OAuth2 provider", type=click.Choice(["microsoft", "google"]))

    client_id = typer.prompt("OAuth2 Client ID")
    client_secret = None
    tenant_id = None

    if provider == "microsoft":
        tenant_id = typer.prompt("Azure AD Tenant ID", default="common")
    elif provider == "google":
        client_secret = typer.prompt("OAuth2 Client Secret", hide_input=True)

    # Provider defaults
    if provider == "microsoft":
        imap_host, imap_port, imap_ssl = "outlook.office365.com", 993, True
        smtp_host, smtp_port, smtp_ssl, smtp_start_ssl = "smtp.office365.com", 587, False, True
    else:  # google
        imap_host, imap_port, imap_ssl = "imap.gmail.com", 993, True
        smtp_host, smtp_port, smtp_ssl, smtp_start_ssl = "smtp.gmail.com", 587, False, True

    console.print(f"\n[bold]Starting OAuth2 device code flow for {provider}...[/bold]")

    from mcp_email_server.oauth2 import get_token_manager

    manager = get_token_manager(provider=provider, client_id=client_id, tenant_id=tenant_id or "common", client_secret=client_secret)

    try:
        flow = manager.initiate_device_code_flow()
    except RuntimeError as e:
        print_error(f"Failed to start OAuth2 flow: {e}")
        raise typer.Exit(1) from e

    console.print(f"\nTo sign in, open: [bold blue]{flow.get('verification_uri', flow.get('verification_url', ''))}")
    console.print(f"Enter code: [bold green]{flow['user_code']}[/bold green]")
    console.print("\nWaiting for authentication...")

    try:
        result = manager.complete_device_code_flow(flow)
    except RuntimeError as e:
        print_error(f"OAuth2 authentication failed: {e}")
        raise typer.Exit(1) from e

    # For Microsoft, the flow result may contain the email
    if result.get("email") and not email_address:
        email_address = result["email"]

    email_settings = EmailSettings(
        account_name=account_name,
        full_name=full_name,
        email_address=email_address,
        incoming=EmailServer(
            user_name=email_address,
            host=imap_host,
            port=imap_port,
            use_ssl=imap_ssl,
        ),
        outgoing=EmailServer(
            user_name=email_address,
            host=smtp_host,
            port=smtp_port,
            use_ssl=smtp_ssl,
            start_ssl=smtp_start_ssl,
        ),
        auth_type="oauth2",
        oauth2_provider=provider,
        oauth2_client_id=client_id,
        oauth2_tenant_id=tenant_id,
        oauth2_client_secret=client_secret,
    )

    settings.add_email(email_settings)
    store_settings(settings)
    print_success(f"Account '{account_name}' added successfully with OAuth2 ({provider}).")
