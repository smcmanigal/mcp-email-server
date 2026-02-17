from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def print_json(data: Any) -> None:
    """Print data as JSON. Accepts Pydantic models or dicts."""
    if hasattr(data, "model_dump_json"):
        console.print_json(data.model_dump_json())
    else:
        import json
        console.print_json(json.dumps(data, default=str))


def print_email_table(emails: list[dict], title: str = "Emails") -> None:
    """Print a table of email metadata."""
    table = Table(title=title, show_lines=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Date", style="green", no_wrap=True)
    table.add_column("From", style="yellow")
    table.add_column("Subject", style="white")
    table.add_column("Attach", style="magenta", no_wrap=True)

    for email in emails:
        date_str = email.get("date", "")
        if hasattr(date_str, "strftime"):
            date_str = date_str.strftime("%Y-%m-%d %H:%M")
        else:
            date_str = str(date_str)[:16]
        attachments = email.get("attachments", [])
        attach_str = str(len(attachments)) if attachments else ""
        table.add_row(
            str(email.get("email_id", "")),
            date_str,
            str(email.get("sender", "")),
            str(email.get("subject", "")),
            attach_str,
        )

    console.print(table)


def print_email_content(email: dict) -> None:
    """Print full email content in a panel."""
    header = (
        f"[cyan]From:[/cyan] {email.get('sender', '')}\n"
        f"[cyan]To:[/cyan] {', '.join(email.get('recipients', []))}\n"
        f"[cyan]Date:[/cyan] {email.get('date', '')}\n"
    )
    if email.get("attachments"):
        header += f"[cyan]Attachments:[/cyan] {', '.join(email['attachments'])}\n"

    console.print(Panel(
        header + "\n" + str(email.get("body", "")),
        title=f"[bold]{email.get('subject', 'No Subject')}[/bold]",
        subtitle=f"ID: {email.get('email_id', '')}",
    ))


def print_success(message: str) -> None:
    console.print(f"[green]{message}[/green]")


def print_error(message: str) -> None:
    console.print(f"[red]Error: {message}[/red]")


def print_folders(folders: list[dict]) -> None:
    """Print a table of email folders."""
    table = Table(title="Folders")
    table.add_column("Name", style="cyan")
    table.add_column("Flags", style="yellow")

    for folder in folders:
        name = folder.get("name", str(folder))
        flags = ", ".join(folder.get("flags", [])) if isinstance(folder, dict) else ""
        table.add_row(name, flags)

    console.print(table)
