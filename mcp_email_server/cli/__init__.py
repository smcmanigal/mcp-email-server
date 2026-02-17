import os

# Default CLI log level to WARNING (MCP server commands keep INFO via their own env)
if "MCP_EMAIL_SERVER_LOG_LEVEL" not in os.environ:
    os.environ["MCP_EMAIL_SERVER_LOG_LEVEL"] = "WARNING"

import typer

from mcp_email_server.app import mcp
from mcp_email_server.cli.accounts import accounts_app
from mcp_email_server.cli.emails import emails_app
from mcp_email_server.cli.flags import flags_app
from mcp_email_server.cli.folders import folders_app
from mcp_email_server.config import delete_settings

app = typer.Typer()
app.add_typer(accounts_app, name="accounts")
app.add_typer(emails_app, name="emails")
app.add_typer(folders_app, name="folders")
app.add_typer(flags_app, name="flags")


@app.command()
def stdio():
    mcp.run(transport="stdio")


@app.command()
def sse(
    host: str = "localhost",
    port: int = 9557,
):
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.run(transport="sse")


@app.command()
def streamable_http(
    host: str = os.environ.get("MCP_HOST", "localhost"),
    port: int = os.environ.get("MCP_PORT", 9557),
):
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.run(transport="streamable-http")


@app.command()
def ui():
    from mcp_email_server.ui import main as ui_main

    ui_main()


@app.command()
def reset():
    delete_settings()
    typer.echo("✅ Config reset")


if __name__ == "__main__":
    app(["stdio"])
