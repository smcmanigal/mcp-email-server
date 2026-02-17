from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from typing import Annotated, Optional

import typer

from mcp_email_server.cli.formatting import (
    console,
    print_email_content,
    print_email_table,
    print_error,
    print_json,
    print_success,
)
from mcp_email_server.emails.dispatcher import dispatch_handler

emails_app = typer.Typer(help="Email operations")


@emails_app.command("list")
def list_emails(
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    mailbox: Annotated[str, typer.Option(help="Mailbox to search")] = "INBOX",
    page: Annotated[int, typer.Option(help="Page number")] = 1,
    page_size: Annotated[int, typer.Option("--page-size", help="Emails per page")] = 10,
    since: Annotated[Optional[datetime], typer.Option(help="Filter emails since datetime")] = None,
    before: Annotated[Optional[datetime], typer.Option(help="Filter emails before datetime")] = None,
    subject: Annotated[Optional[str], typer.Option(help="Filter by subject")] = None,
    from_address: Annotated[Optional[str], typer.Option("--from", help="Filter by sender address")] = None,
    seen: Annotated[Optional[bool], typer.Option(help="Filter by read status")] = None,
    flagged: Annotated[Optional[bool], typer.Option(help="Filter by flagged status")] = None,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
) -> None:
    """List email metadata (paginated)."""
    try:
        handler = dispatch_handler(account)
        result = asyncio.run(handler.get_emails_metadata(
            page=page,
            page_size=page_size,
            before=before,
            since=since,
            subject=subject,
            from_address=from_address,
            mailbox=mailbox,
            seen=seen,
            flagged=flagged,
        ))
        if json_output:
            print_json(result)
        else:
            print_email_table(
                [e.model_dump() for e in result.emails],
                title=f"Emails in {mailbox} (page {result.page}/{max((result.total + page_size - 1) // page_size, 1)}, total: {result.total})",
            )
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)


@emails_app.command("read")
def read_emails(
    email_ids: Annotated[list[str], typer.Argument(help="Email IDs to read")],
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    mailbox: Annotated[str, typer.Option(help="Mailbox to search")] = "INBOX",
    truncate: Annotated[Optional[int], typer.Option(help="Max body characters")] = None,
    json_output: Annotated[bool, typer.Option("--json", "-j", help="Output as JSON")] = False,
) -> None:
    """Get full email content by ID."""
    try:
        handler = dispatch_handler(account)
        result = asyncio.run(handler.get_emails_content(email_ids, mailbox, truncate))
        if json_output:
            print_json(result)
        else:
            for email in result.emails:
                print_email_content(email.model_dump())
            if result.failed_ids:
                print_error(f"Failed to retrieve: {', '.join(result.failed_ids)}")
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)


@emails_app.command("send")
def send_email(
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    to: Annotated[list[str], typer.Option("--to", help="Recipient email addresses")],
    subject: Annotated[str, typer.Option("--subject", "-s", help="Email subject")],
    body: Annotated[Optional[str], typer.Option("--body", "-b", help="Email body (reads from stdin if omitted)")] = None,
    cc: Annotated[Optional[list[str]], typer.Option("--cc", help="CC email addresses")] = None,
    bcc: Annotated[Optional[list[str]], typer.Option("--bcc", help="BCC email addresses")] = None,
    html: Annotated[bool, typer.Option("--html", help="Send as HTML")] = False,
    attachment: Annotated[Optional[list[str]], typer.Option("--attachment", help="File paths to attach")] = None,
    reply_to: Annotated[Optional[str], typer.Option("--reply-to", help="Message-ID to reply to")] = None,
) -> None:
    """Send an email."""
    try:
        if body is None:
            if sys.stdin.isatty():
                print_error("No --body provided and stdin is a terminal. Provide --body or pipe content via stdin.")
                raise typer.Exit(1)
            body = sys.stdin.read()

        handler = dispatch_handler(account)
        asyncio.run(handler.send_email(
            recipients=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            html=html,
            attachments=attachment,
            in_reply_to=reply_to,
        ))
        recipient_str = ", ".join(to)
        attachment_info = f" with {len(attachment)} attachment(s)" if attachment else ""
        print_success(f"Email sent to {recipient_str}{attachment_info}")
    except typer.Exit:
        raise
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)


@emails_app.command("delete")
def delete_emails(
    email_ids: Annotated[list[str], typer.Argument(help="Email IDs to delete")],
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    mailbox: Annotated[str, typer.Option(help="Mailbox to delete from")] = "INBOX",
) -> None:
    """Delete emails by ID."""
    try:
        handler = dispatch_handler(account)
        deleted_ids, failed_ids = asyncio.run(handler.delete_emails(email_ids, mailbox))
        print_success(f"Deleted {len(deleted_ids)} email(s)")
        if failed_ids:
            print_error(f"Failed to delete: {', '.join(failed_ids)}")
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)


@emails_app.command("move")
def move_emails(
    email_ids: Annotated[list[str], typer.Argument(help="Email IDs to move")],
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    target_folder: Annotated[str, typer.Option("--target-folder", "-t", help="Target folder")],
    source_mailbox: Annotated[str, typer.Option("--source-mailbox", help="Source mailbox")] = "INBOX",
    create: Annotated[bool, typer.Option("--create/--no-create", help="Create folder if missing")] = True,
) -> None:
    """Move emails to a folder."""
    try:
        handler = dispatch_handler(account)
        result = asyncio.run(handler.move_emails_to_folder(
            email_ids=email_ids,
            target_folder=target_folder,
            source_mailbox=source_mailbox,
            create_if_missing=create,
        ))
        print_success(f"Moved {len(result['moved'])} email(s) to {target_folder}")
        if result["failed"]:
            print_error(f"Failed to move: {', '.join(result['failed'])}")
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)


@emails_app.command("save")
def save_email(
    email_id: Annotated[str, typer.Argument(help="Email ID to save")],
    file_path: Annotated[str, typer.Argument(help="File path to save to")],
    account: Annotated[str, typer.Option("--account", "-a", help="Email account name")],
    mailbox: Annotated[str, typer.Option(help="Mailbox to search")] = "INBOX",
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: markdown or html")] = "markdown",
    headers: Annotated[bool, typer.Option("--headers/--no-headers", help="Include email headers")] = True,
) -> None:
    """Save an email to a file."""
    try:
        handler = dispatch_handler(account)
        result = asyncio.run(handler.save_email_to_file(
            email_id=email_id,
            file_path=file_path,
            output_format=format,
            include_headers=headers,
            mailbox=mailbox,
        ))
        print_success(f"Saved email {result.email_id} to {result.file_path} ({result.content_length} chars, {result.output_format})")
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1)
