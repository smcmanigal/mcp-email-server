from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from mcp_email_server.config import (
    AccountAttributes,
    EmailSettings,
    ProviderSettings,
    get_settings,
)
from mcp_email_server.emails.dispatcher import dispatch_handler
from mcp_email_server.emails.models import (
    AttachmentDownloadResponse,
    EmailContentBatchResponse,
    EmailMetadataPageResponse,
    SaveEmailToFileResponse,
)

mcp = FastMCP("email")


@mcp.resource("email://{account_name}")
async def get_account(account_name: str) -> EmailSettings | ProviderSettings | None:
    settings = get_settings()
    return settings.get_account(account_name, masked=True)


@mcp.tool(description="List all configured email accounts with masked credentials.")
async def list_available_accounts() -> list[AccountAttributes]:
    settings = get_settings()
    return [account.masked() for account in settings.get_accounts()]


@mcp.tool(description="Add a new email account configuration to the settings.")
async def add_email_account(email: EmailSettings) -> str:
    settings = get_settings()
    settings.add_email(email)
    settings.store()
    return f"Successfully added email account '{email.account_name}'"


@mcp.tool(
    description="List email metadata (email_id, subject, sender, recipients, date) without body content. Returns email_id for use with get_emails_content."
)
async def list_emails_metadata(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    page: Annotated[
        int,
        Field(default=1, description="The page number to retrieve (starting from 1)."),
    ] = 1,
    page_size: Annotated[int, Field(default=10, description="The number of emails to retrieve per page.")] = 10,
    before: Annotated[
        datetime | None,
        Field(default=None, description="Retrieve emails before this datetime (UTC)."),
    ] = None,
    since: Annotated[
        datetime | None,
        Field(default=None, description="Retrieve emails since this datetime (UTC)."),
    ] = None,
    subject: Annotated[str | None, Field(default=None, description="Filter emails by subject.")] = None,
    from_address: Annotated[str | None, Field(default=None, description="Filter emails by sender address.")] = None,
    to_address: Annotated[
        str | None,
        Field(default=None, description="Filter emails by recipient address."),
    ] = None,
    order: Annotated[
        Literal["asc", "desc"],
        Field(default=None, description="Order emails by field. `asc` or `desc`."),
    ] = "desc",
    mailbox: Annotated[str, Field(default="INBOX", description="The mailbox to search.")] = "INBOX",
    seen: Annotated[
        bool | None,
        Field(default=None, description="Filter by read status: True=read, False=unread, None=all."),
    ] = None,
    flagged: Annotated[
        bool | None,
        Field(default=None, description="Filter by flagged/starred status: True=flagged, False=unflagged, None=all."),
    ] = None,
    answered: Annotated[
        bool | None,
        Field(default=None, description="Filter by replied status: True=replied, False=not replied, None=all."),
    ] = None,
) -> EmailMetadataPageResponse:
    handler = dispatch_handler(account_name)

    return await handler.get_emails_metadata(
        page=page,
        page_size=page_size,
        before=before,
        since=since,
        subject=subject,
        from_address=from_address,
        to_address=to_address,
        order=order,
        mailbox=mailbox,
        seen=seen,
        flagged=flagged,
        answered=answered,
    )


@mcp.tool(
    description="Get the full content (including body) of one or more emails by their email_id. Use list_emails_metadata first to get the email_id."
)
async def get_emails_content(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[
        list[str],
        Field(
            description="List of email_id to retrieve (obtained from list_emails_metadata). Can be a single email_id or multiple email_ids."
        ),
    ],
    mailbox: Annotated[str, Field(default="INBOX", description="The mailbox to retrieve emails from.")] = "INBOX",
    truncate_body: Annotated[
        int | None,
        Field(
            default=None,
            description="Maximum number of characters for email body content. If specified, body content longer than this will be truncated. If not specified, defaults to 20000 characters.",
        ),
    ] = None,
) -> EmailContentBatchResponse:
    handler = dispatch_handler(account_name)
    return await handler.get_emails_content(email_ids, mailbox, truncate_body)


@mcp.tool(
    description="Send an email using the specified account. Supports replying to emails with proper threading when in_reply_to is provided.",
)
async def send_email(
    account_name: Annotated[str, Field(description="The name of the email account to send from.")],
    recipients: Annotated[list[str], Field(description="A list of recipient email addresses.")],
    subject: Annotated[str, Field(description="The subject of the email.")],
    body: Annotated[str, Field(description="The body of the email.")],
    cc: Annotated[
        list[str] | None,
        Field(default=None, description="A list of CC email addresses."),
    ] = None,
    bcc: Annotated[
        list[str] | None,
        Field(default=None, description="A list of BCC email addresses."),
    ] = None,
    html: Annotated[
        bool,
        Field(default=False, description="Whether to send the email as HTML (True) or plain text (False)."),
    ] = False,
    attachments: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="A list of absolute file paths to attach to the email. Supports common file types (documents, images, archives, etc.).",
        ),
    ] = None,
    in_reply_to: Annotated[
        str | None,
        Field(
            default=None,
            description="Message-ID of the email being replied to. Enables proper threading in email clients.",
        ),
    ] = None,
    references: Annotated[
        str | None,
        Field(
            default=None,
            description="Space-separated Message-IDs for the thread chain. Usually includes in_reply_to plus ancestors.",
        ),
    ] = None,
) -> str:
    handler = dispatch_handler(account_name)
    await handler.send_email(
        recipients,
        subject,
        body,
        cc,
        bcc,
        html,
        attachments,
        in_reply_to,
        references,
    )
    recipient_str = ", ".join(recipients)
    attachment_info = f" with {len(attachments)} attachment(s)" if attachments else ""
    return f"Email sent successfully to {recipient_str}{attachment_info}"


@mcp.tool(
    description="Delete one or more emails by their email_id. Use list_emails_metadata first to get the email_id."
)
async def delete_emails(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[
        list[str],
        Field(description="List of email_id to delete (obtained from list_emails_metadata)."),
    ],
    mailbox: Annotated[str, Field(default="INBOX", description="The mailbox to delete emails from.")] = "INBOX",
) -> str:
    handler = dispatch_handler(account_name)
    deleted_ids, failed_ids = await handler.delete_emails(email_ids, mailbox)

    result = f"Successfully deleted {len(deleted_ids)} email(s)"
    if failed_ids:
        result += f", failed to delete {len(failed_ids)} email(s): {', '.join(failed_ids)}"
    return result


@mcp.tool(description="List all available email folders/labels in the account.")
async def list_email_folders(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    pattern: Annotated[str, Field(default="*", description="Pattern to filter folders (default: '*' for all)")] = "*",
) -> list[dict[str, Any]]:
    handler = dispatch_handler(account_name)
    return await handler.list_folders(pattern)


@mcp.tool(description="Move one or more emails to a specific folder.")
async def move_emails_to_folder(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[
        list[str | int],
        Field(description="List of email IDs to move."),
    ],
    target_folder: Annotated[str, Field(description="The target folder name.")],
    source_mailbox: Annotated[
        str,
        Field(default="INBOX", description="The source mailbox (default: INBOX)."),
    ] = "INBOX",
    create_if_missing: Annotated[
        bool,
        Field(default=True, description="Create folder if it doesn't exist"),
    ] = True,
) -> dict[str, Any]:
    handler = dispatch_handler(account_name)
    id_strings = [str(eid) for eid in email_ids]
    result = await handler.move_emails_to_folder(id_strings, target_folder, source_mailbox, create_if_missing)
    return {
        "moved": result["moved"],
        "failed": result["failed"],
        "total_moved": len(result["moved"]),
        "total_failed": len(result["failed"]),
    }


@mcp.tool(
    description="Download an email attachment and save it to the specified path. This feature must be explicitly enabled in settings (enable_attachment_download=true) due to security considerations.",
)
async def download_attachment(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_id: Annotated[
        str, Field(description="The email ID (obtained from list_emails_metadata or get_emails_content).")
    ],
    attachment_name: Annotated[
        str, Field(description="The name of the attachment to download (as shown in the attachments list).")
    ],
    save_path: Annotated[str, Field(description="The absolute path where the attachment should be saved.")],
    mailbox: Annotated[str, Field(description="The mailbox to search in (default: INBOX).")] = "INBOX",
) -> AttachmentDownloadResponse:
    settings = get_settings()
    if not settings.enable_attachment_download:
        msg = (
            "Attachment download is disabled. Set 'enable_attachment_download=true' in settings to enable this feature."
        )
        raise PermissionError(msg)

    handler = dispatch_handler(account_name)
    return await handler.download_attachment(email_id, attachment_name, save_path, mailbox)


@mcp.tool(description="Add flags to one or more emails.")
async def add_email_flags(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[list[str | int], Field(description="List of email IDs to add flags to.")],
    flags: Annotated[list[str], Field(description="List of flags to add (e.g., ['ProcessedByBot', 'Seen']).")],
    silent: Annotated[
        bool, Field(default=False, description="Use silent operation to suppress server responses (default: False)")
    ] = False,
) -> dict[str, Any]:
    handler = dispatch_handler(account_name)
    id_strings = [str(eid) for eid in email_ids]
    results = await handler.add_flags(id_strings, flags, silent)
    successful = sum(results.values())
    return {
        "results": results,
        "total_modified": successful,
        "failed": len(email_ids) - successful,
        "operation": "add_flags",
        "flags": flags,
        "silent": silent,
    }


@mcp.tool(description="Remove flags from one or more emails.")
async def remove_email_flags(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[list[str | int], Field(description="List of email IDs to remove flags from.")],
    flags: Annotated[list[str], Field(description="List of flags to remove (e.g., ['ProcessedByBot', 'Flagged']).")],
    silent: Annotated[
        bool, Field(default=False, description="Use silent operation to suppress server responses (default: False)")
    ] = False,
) -> dict[str, Any]:
    handler = dispatch_handler(account_name)
    id_strings = [str(eid) for eid in email_ids]
    results = await handler.remove_flags(id_strings, flags, silent)
    successful = sum(results.values())
    return {
        "results": results,
        "total_modified": successful,
        "failed": len(email_ids) - successful,
        "operation": "remove_flags",
        "flags": flags,
        "silent": silent,
    }


@mcp.tool(description="Replace all flags on one or more emails with the specified flags.")
async def replace_email_flags(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[list[str | int], Field(description="List of email IDs to replace flags on.")],
    flags: Annotated[list[str], Field(description="List of flags to set (replaces all existing flags).")],
    silent: Annotated[
        bool, Field(default=False, description="Use silent operation to suppress server responses (default: False)")
    ] = False,
) -> dict[str, Any]:
    handler = dispatch_handler(account_name)
    id_strings = [str(eid) for eid in email_ids]
    results = await handler.replace_flags(id_strings, flags, silent)
    successful = sum(results.values())
    return {
        "results": results,
        "total_modified": successful,
        "failed": len(email_ids) - successful,
        "operation": "replace_flags",
        "flags": flags,
        "silent": silent,
    }


@mcp.tool(description="Save a complete email to a file without truncation.")
async def save_email_to_file(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_id: Annotated[
        str, Field(description="The email ID (obtained from list_emails_metadata or get_emails_content).")
    ],
    file_path: Annotated[str, Field(description="The file path where to save the email content.")],
    output_format: Annotated[
        Literal["html", "markdown"],
        Field(
            default="markdown",
            description="Output format: 'html' returns original content, 'markdown' converts HTML to markdown or returns plain text as-is.",
        ),
    ] = "markdown",
    include_headers: Annotated[
        bool,
        Field(
            default=True,
            description="Include email headers (subject, from, date, etc.) in the saved file.",
        ),
    ] = True,
    mailbox: Annotated[str, Field(default="INBOX", description="The mailbox to search in.")] = "INBOX",
) -> SaveEmailToFileResponse:
    handler = dispatch_handler(account_name)
    return await handler.save_email_to_file(
        email_id=email_id,
        file_path=file_path,
        output_format=output_format,
        include_headers=include_headers,
        mailbox=mailbox,
    )
