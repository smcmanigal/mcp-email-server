from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from mcp_email_server.cli.formatting import console, print_error, print_folders, print_json
from mcp_email_server.emails.dispatcher import dispatch_handler

folders_app = typer.Typer(help="Folder operations")


@folders_app.command("list")
def list_folders(
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    pattern: Annotated[str, typer.Option(help="Pattern to filter folders")] = "*",
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
) -> None:
    """List mailbox folders."""
    try:
        handler = dispatch_handler(account)
        result = asyncio.run(handler.list_folders(pattern=pattern))
        if json_output:
            print_json(result)
        else:
            print_folders(result)
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(code=1) from None
