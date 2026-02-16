import abc
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp_email_server.emails.models import (
        AttachmentDownloadResponse,
        EmailContentBatchResponse,
        EmailMetadataPageResponse,
        SaveEmailToFileResponse,
    )


class EmailHandler(abc.ABC):
    @abc.abstractmethod
    async def get_emails_metadata(
        self,
        page: int = 1,
        page_size: int = 10,
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
        order: str = "desc",
        mailbox: str = "INBOX",
        seen: bool | None = None,
        flagged: bool | None = None,
        answered: bool | None = None,
    ) -> "EmailMetadataPageResponse":
        """
        Get email metadata only (without body content) for better performance.

        Args:
            page: Page number (starting from 1).
            page_size: Number of emails per page.
            before: Filter emails before this datetime.
            since: Filter emails since this datetime.
            subject: Filter by subject (substring match).
            from_address: Filter by sender address.
            to_address: Filter by recipient address.
            order: Sort order ('asc' or 'desc').
            mailbox: Mailbox to search (default: 'INBOX').
            seen: Filter by read status (True=read, False=unread, None=all).
            flagged: Filter by flagged/starred status (True=flagged, False=unflagged, None=all).
            answered: Filter by replied status (True=replied, False=not replied, None=all).
        """

    @abc.abstractmethod
    async def get_emails_content(
        self, email_ids: list[str], mailbox: str = "INBOX", truncate_body: int | None = None
    ) -> "EmailContentBatchResponse":
        """
        Get full content (including body) of multiple emails by their email IDs (IMAP UIDs)

        Args:
            email_ids: List of email IDs (IMAP UIDs) to retrieve.
            mailbox: The mailbox to retrieve emails from (default: 'INBOX').
            truncate_body: Maximum number of characters for email body content.
                If specified, body content longer than this will be truncated.
                If None, uses the default MAX_BODY_LENGTH (20000).
        """

    @abc.abstractmethod
    async def send_email(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: bool = False,
        attachments: list[str] | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> None:
        """
        Send email

        Args:
            recipients: List of recipient email addresses.
            subject: Email subject.
            body: Email body content.
            cc: List of CC email addresses.
            bcc: List of BCC email addresses.
            html: Whether to send as HTML (True) or plain text (False).
            attachments: List of file paths to attach.
            in_reply_to: Message-ID of the email being replied to (for threading).
            references: Space-separated Message-IDs for the thread chain.
        """

    @abc.abstractmethod
    async def delete_emails(self, email_ids: list[str], mailbox: str = "INBOX") -> tuple[list[str], list[str]]:
        """
        Delete emails by their IDs. Returns (deleted_ids, failed_ids)
        """

    @abc.abstractmethod
    async def list_folders(self, pattern: str = "*") -> list[dict[str, Any]]:
        """
        List available email folders/labels in the account.

        Args:
            pattern: Pattern to filter folders (default: '*' for all).
        """

    @abc.abstractmethod
    async def move_emails_to_folder(
        self,
        email_ids: list[str],
        target_folder: str,
        source_mailbox: str = "INBOX",
        create_if_missing: bool = True,
    ) -> dict[str, list[str]]:
        """
        Move one or more emails to a specified folder.

        Args:
            email_ids: List of email IDs (IMAP UIDs) to move.
            target_folder: The target folder to move emails to.
            source_mailbox: The source mailbox (default: "INBOX").
            create_if_missing: Create the target folder if it doesn't exist.

        Returns:
            Dict with 'moved' and 'failed' lists of email IDs.
        """

    @abc.abstractmethod
    async def download_attachment(
        self,
        email_id: str,
        attachment_name: str,
        save_path: str,
        mailbox: str = "INBOX",
    ) -> "AttachmentDownloadResponse":
        """
        Download an email attachment and save it to the specified path.

        Args:
            email_id: The UID of the email containing the attachment.
            attachment_name: The filename of the attachment to download.
            save_path: The local path where the attachment will be saved.
            mailbox: The mailbox to search in (default: "INBOX").

        Returns:
            AttachmentDownloadResponse with download result information.
        """

    @abc.abstractmethod
    async def add_flags(self, email_ids: list[str], flags: list[str], silent: bool = False) -> dict[str, bool]:
        """Add flags to emails."""

    @abc.abstractmethod
    async def remove_flags(self, email_ids: list[str], flags: list[str], silent: bool = False) -> dict[str, bool]:
        """Remove flags from emails."""

    @abc.abstractmethod
    async def replace_flags(self, email_ids: list[str], flags: list[str], silent: bool = False) -> dict[str, bool]:
        """Replace all flags on emails."""

    @abc.abstractmethod
    async def save_email_to_file(
        self,
        email_id: str,
        file_path: str,
        output_format: str = "markdown",
        include_headers: bool = True,
        mailbox: str = "INBOX",
    ) -> "SaveEmailToFileResponse":
        """
        Save a complete email to a file without truncation.

        Args:
            email_id: The UID of the email to save.
            file_path: The file path where to save the email content.
            output_format: 'html' for original HTML, 'markdown' to convert HTML to markdown.
            include_headers: Include email headers (subject, from, date, etc.).
            mailbox: The mailbox to search in (default: "INBOX").
        """
