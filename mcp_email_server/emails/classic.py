import asyncio
import email.utils
import mimetypes
import re
import ssl
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from typing import Any

import aioimaplib
import aiosmtplib

from mcp_email_server.config import EmailServer, EmailSettings
from mcp_email_server.emails import EmailHandler
from mcp_email_server.emails.models import (
    AttachmentDownloadResponse,
    EmailBodyResponse,
    EmailContentBatchResponse,
    EmailMetadata,
    EmailMetadataPageResponse,
    SaveEmailToFileResponse,
)
from mcp_email_server.log import logger
from mcp_email_server.utils.html_converter import html_to_markdown

# Maximum body length before truncation (characters)
MAX_BODY_LENGTH = 20000


def _quote_mailbox(mailbox: str) -> str:
    """Quote mailbox name for IMAP compatibility.

    Some IMAP servers (notably Proton Mail Bridge) require mailbox names
    to be quoted. This is valid per RFC 3501 and works with all IMAP servers.

    Per RFC 3501 Section 9 (Formal Syntax), quoted strings must escape
    backslashes and double-quote characters with a preceding backslash.

    See: https://github.com/ai-zerolab/mcp-email-server/issues/87
    See: https://www.rfc-editor.org/rfc/rfc3501#section-9
    """
    # Per RFC 3501, literal double-quote characters in a quoted string must
    # be escaped with a backslash. Backslashes themselves must also be escaped.
    escaped = mailbox.replace("\\", "\\\\").replace('"', r"\"")
    return f'"{escaped}"'


def _quote_search_param(param: str) -> str:
    """Quote and escape IMAP search parameter per RFC 3501 Section 9."""
    escaped = param.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


async def _send_imap_id(imap: aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL) -> None:
    """Send IMAP ID command with fallback for strict servers like 163.com.

    aioimaplib's id() method sends ID command with spaces between parentheses
    and content (e.g., 'ID ( "name" "value" )'), which some strict IMAP servers
    like 163.com reject with 'BAD Parse command error'.

    This function first tries the standard id() method, and if it fails,
    falls back to sending a raw command with correct format.

    See: https://github.com/ai-zerolab/mcp-email-server/issues/85
    """
    try:
        response = await imap.id(name="mcp-email-server", version="1.0.0")
        if response.result != "OK":
            # Fallback for strict servers (e.g., 163.com)
            # Send raw command with correct parenthesis format
            await imap.protocol.execute(
                aioimaplib.Command(
                    "ID",
                    imap.protocol.new_tag(),
                    '("name" "mcp-email-server" "version" "1.0.0")',
                )
            )
    except Exception as e:
        logger.warning(f"IMAP ID command failed: {e!s}")


def _create_smtp_ssl_context(verify_ssl: bool) -> ssl.SSLContext | None:
    """Create SSL context for SMTP connections.

    Returns None for default verification, or permissive context
    for self-signed certificates when verify_ssl=False.
    """
    if verify_ssl:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class EmailClient:
    def __init__(self, email_server: EmailServer, sender: str | None = None):
        self.email_server = email_server
        self.sender = sender or email_server.user_name

        self.imap_class = aioimaplib.IMAP4_SSL if self.email_server.use_ssl else aioimaplib.IMAP4

        self.smtp_use_tls = self.email_server.use_ssl
        self.smtp_start_tls = self.email_server.start_ssl
        self.smtp_verify_ssl = self.email_server.verify_ssl

    def _get_smtp_ssl_context(self) -> ssl.SSLContext | None:
        """Get SSL context for SMTP connections based on verify_ssl setting."""
        return _create_smtp_ssl_context(self.smtp_verify_ssl)

    @staticmethod
    def _parse_recipients(email_message) -> list[str]:
        """Extract recipient addresses from To and Cc headers."""
        recipients = []
        to_header = email_message.get("To", "")
        if to_header:
            recipients = [addr.strip() for addr in to_header.split(",")]
        cc_header = email_message.get("Cc", "")
        if cc_header:
            recipients.extend([addr.strip() for addr in cc_header.split(",")])
        return recipients

    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        """Parse email date string to datetime, with fallback to current time."""
        try:
            date_tuple = email.utils.parsedate_tz(date_str)
            if date_tuple:
                return datetime.fromtimestamp(email.utils.mktime_tz(date_tuple), tz=timezone.utc)
            return datetime.now(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    def _parse_email_data(self, raw_email: bytes, email_id: str | None = None, truncate_body: int | None = None) -> dict[str, Any]:  # noqa: C901
        """Parse raw email data into a structured dictionary."""
        parser = BytesParser(policy=default)
        email_message = parser.parsebytes(raw_email)

        # Extract email parts
        subject = email_message.get("Subject", "")
        sender = email_message.get("From", "")
        date_str = email_message.get("Date", "")

        # Extract Message-ID for reply threading
        message_id = email_message.get("Message-ID")

        # Extract recipients and parse date
        to_addresses = self._parse_recipients(email_message)
        date = self._parse_date(date_str)

        # Get body content
        body = ""
        html_body = ""  # Fallback if no text/plain
        attachments = []

        def _strip_html(html: str) -> str:
            """Simple HTML to text conversion."""
            import re

            # Remove script and style elements
            text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
            # Convert common block elements to newlines
            text = re.sub(r"<(br|p|div|tr|li)[^>]*/?>", "\n", text, flags=re.IGNORECASE)
            # Remove all remaining HTML tags
            text = re.sub(r"<[^>]+>", "", text)
            # Decode common HTML entities
            text = text.replace("&nbsp;", " ").replace("&amp;", "&")
            text = text.replace("&lt;", "<").replace("&gt;", ">")
            text = text.replace("&quot;", '"').replace("&#39;", "'")
            # Collapse multiple newlines and whitespace
            text = re.sub(r"\n\s*\n", "\n\n", text)
            text = re.sub(r" +", " ", text)
            return text.strip()

        if email_message.is_multipart():
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                # Handle attachments
                if "attachment" in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        attachments.append(filename)
                # Handle text parts - prefer text/plain
                elif content_type == "text/plain":
                    body_part = part.get_payload(decode=True)
                    if body_part:
                        charset = part.get_content_charset("utf-8")
                        try:
                            body += body_part.decode(charset)
                        except UnicodeDecodeError:
                            body += body_part.decode("utf-8", errors="replace")
                # Collect HTML as fallback
                elif content_type == "text/html" and not body:
                    html_part = part.get_payload(decode=True)
                    if html_part:
                        charset = part.get_content_charset("utf-8")
                        try:
                            html_body += html_part.decode(charset)
                        except UnicodeDecodeError:
                            html_body += html_part.decode("utf-8", errors="replace")

            # Fall back to HTML if no plain text found
            if not body and html_body:
                body = _strip_html(html_body)
        else:
            # Handle single-part emails
            content_type = email_message.get_content_type()
            payload = email_message.get_payload(decode=True)
            if payload:
                charset = email_message.get_content_charset("utf-8")
                try:
                    text = payload.decode(charset)
                except UnicodeDecodeError:
                    text = payload.decode("utf-8", errors="replace")

                body = _strip_html(text) if content_type == "text/html" else text
        limit = truncate_body if truncate_body is not None else MAX_BODY_LENGTH
        if body and len(body) > limit:
            body = body[:limit] + "...[TRUNCATED]"
        return {
            "email_id": email_id or "",
            "message_id": message_id,
            "subject": subject,
            "from": sender,
            "to": to_addresses,
            "body": body,
            "date": date,
            "attachments": attachments,
        }

    @staticmethod
    def _build_search_criteria(
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        body: str | None = None,
        text: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
        seen: bool | None = None,
        flagged: bool | None = None,
        answered: bool | None = None,
    ) -> list[str]:
        search_criteria = []
        if before:
            search_criteria.extend(["BEFORE", before.strftime("%d-%b-%Y").upper()])
        if since:
            search_criteria.extend(["SINCE", since.strftime("%d-%b-%Y").upper()])
        if subject:
            search_criteria.extend(["SUBJECT", _quote_search_param(subject)])
        if body:
            search_criteria.extend(["BODY", _quote_search_param(body)])
        if text:
            search_criteria.extend(["TEXT", _quote_search_param(text)])
        if from_address:
            search_criteria.extend(["FROM", _quote_search_param(from_address)])
        if to_address:
            search_criteria.extend(["TO", _quote_search_param(to_address)])

        # Flag-based criteria using mapping to reduce complexity
        flag_criteria = [
            (seen, {True: "SEEN", False: "UNSEEN"}),
            (flagged, {True: "FLAGGED", False: "UNFLAGGED"}),
            (answered, {True: "ANSWERED", False: "UNANSWERED"}),
        ]
        for flag_value, criteria_map in flag_criteria:
            if flag_value in criteria_map:
                search_criteria.append(criteria_map[flag_value])

        return search_criteria or ["ALL"]

    def _parse_headers(self, email_id: str, raw_headers: bytes) -> dict[str, Any] | None:
        """Parse raw email headers into metadata dictionary."""
        try:
            parser = BytesParser(policy=default)
            email_message = parser.parsebytes(raw_headers)

            subject = email_message.get("Subject", "")
            sender = email_message.get("From", "")
            date_str = email_message.get("Date", "")

            to_addresses = self._parse_recipients(email_message)
            date = self._parse_date(date_str)

            return {
                "email_id": email_id,
                "subject": subject,
                "from": sender,
                "to": to_addresses,
                "date": date,
                "attachments": [],
            }
        except Exception as e:
            logger.error(f"Error parsing email headers: {e!s}")
            return None

    async def _fetch_dates_chunk(
        self,
        imap: aioimaplib.IMAP4_SSL | aioimaplib.IMAP4,
        chunk: list[bytes],
        chunk_num: int,
        total_chunks: int,
    ) -> dict[str, datetime]:
        """Fetch INTERNALDATE for a single chunk of UIDs."""
        uid_list = ",".join(uid.decode() for uid in chunk)
        chunk_start = time.perf_counter()
        _, data = await imap.uid("fetch", uid_list, "(INTERNALDATE)")
        chunk_elapsed = time.perf_counter() - chunk_start

        chunk_dates: dict[str, datetime] = {}
        for item in data:
            if not isinstance(item, bytes) or b"INTERNALDATE" not in item:
                continue
            uid_match = re.search(rb"UID (\d+)", item)
            date_match = re.search(rb'INTERNALDATE "([^"]+)"', item)
            if uid_match and date_match:
                uid = uid_match.group(1).decode()
                date_str = date_match.group(1).decode().strip()
                chunk_dates[uid] = datetime.strptime(date_str, "%d-%b-%Y %H:%M:%S %z")

        if total_chunks > 1:
            logger.info(f"Fetched dates chunk {chunk_num}/{total_chunks}: {len(chunk)} UIDs in {chunk_elapsed:.2f}s")

        return chunk_dates

    async def _batch_fetch_dates(
        self,
        imap: aioimaplib.IMAP4_SSL | aioimaplib.IMAP4,
        email_ids: list[bytes],
        chunk_size: int = 5000,
    ) -> dict[str, datetime]:
        """Batch fetch INTERNALDATE for all UIDs in parallel chunks."""
        if not email_ids:
            return {}

        # Split into chunks
        chunks = [email_ids[i : i + chunk_size] for i in range(0, len(email_ids), chunk_size)]
        total_chunks = len(chunks)

        # Fetch all chunks in parallel
        tasks = [
            self._fetch_dates_chunk(imap, chunk, chunk_num, total_chunks) for chunk_num, chunk in enumerate(chunks, 1)
        ]
        results = await asyncio.gather(*tasks)

        # Merge results
        uid_dates: dict[str, datetime] = {}
        for chunk_dates in results:
            uid_dates.update(chunk_dates)

        return uid_dates

    async def _batch_fetch_headers(
        self,
        imap: aioimaplib.IMAP4_SSL | aioimaplib.IMAP4,
        email_ids: list[bytes] | list[str],
    ) -> dict[str, dict[str, Any]]:
        """Batch fetch headers for a list of UIDs."""
        if not email_ids:
            return {}

        # Normalize to list of strings
        str_ids = [uid.decode() if isinstance(uid, bytes) else uid for uid in email_ids]
        uid_list = ",".join(str_ids)
        _, data = await imap.uid("fetch", uid_list, "BODY.PEEK[HEADER]")

        results: dict[str, dict[str, Any]] = {}
        for i, item in enumerate(data):
            if not isinstance(item, bytes) or b"BODY[HEADER]" not in item:
                continue
            # First try to find UID in the same line (standard format)
            uid_match = re.search(rb"UID (\d+)", item)
            if uid_match and i + 1 < len(data) and isinstance(data[i + 1], bytearray):
                uid = uid_match.group(1).decode()
                raw_headers = bytes(data[i + 1])
                metadata = self._parse_headers(uid, raw_headers)
                if metadata:
                    results[uid] = metadata
            # Proton Bridge format: UID comes AFTER header data in a separate item
            # Format: [i]=b'N FETCH (BODY[HEADER] {size}', [i+1]=bytearray(headers), [i+2]=b' UID xxx)'
            elif i + 2 < len(data) and isinstance(data[i + 1], bytearray):
                uid_after_match = re.search(rb"UID (\d+)", data[i + 2]) if isinstance(data[i + 2], bytes) else None
                if uid_after_match:
                    uid = uid_after_match.group(1).decode()
                    raw_headers = bytes(data[i + 1])
                    metadata = self._parse_headers(uid, raw_headers)
                    if metadata:
                        results[uid] = metadata

        return results

    async def get_email_count(
        self,
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
        mailbox: str = "INBOX",
        seen: bool | None = None,
        flagged: bool | None = None,
        answered: bool | None = None,
    ) -> int:
        imap = self.imap_class(self.email_server.host, self.email_server.port)
        try:
            # Wait for the connection to be established
            await imap._client_task
            await imap.wait_hello_from_server()

            # Login and select inbox
            await imap.login(self.email_server.user_name, self.email_server.password)
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))
            search_criteria = self._build_search_criteria(
                before,
                since,
                subject,
                from_address=from_address,
                to_address=to_address,
                seen=seen,
                flagged=flagged,
                answered=answered,
            )
            logger.info(f"Count: Search criteria: {search_criteria}")
            # Search for messages and count them - use UID SEARCH for consistency
            _, messages = await imap.uid_search(*search_criteria)
            return len(messages[0].split())
        finally:
            # Ensure we logout properly
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

    async def get_emails_metadata_stream(
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
    ) -> AsyncGenerator[dict[str, Any], None]:
        imap = self.imap_class(self.email_server.host, self.email_server.port)
        try:
            # Wait for the connection to be established
            await imap._client_task
            await imap.wait_hello_from_server()

            # Login and select mailbox
            await imap.login(self.email_server.user_name, self.email_server.password)
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))

            search_criteria = self._build_search_criteria(
                before,
                since,
                subject,
                from_address=from_address,
                to_address=to_address,
                seen=seen,
                flagged=flagged,
                answered=answered,
            )
            logger.info(f"Get metadata: Search criteria: {search_criteria}")

            # Search for messages - use UID SEARCH for better compatibility
            _, messages = await imap.uid_search(*search_criteria)

            # Handle empty or None responses
            if not messages or not messages[0]:
                logger.warning("No messages returned from search")
                return

            email_ids = messages[0].split()
            logger.info(f"Found {len(email_ids)} email IDs")

            # Phase 1: Batch fetch INTERNALDATE for sorting (parallel chunks)
            fetch_dates_start = time.perf_counter()
            uid_dates = await self._batch_fetch_dates(imap, email_ids)
            fetch_dates_elapsed = time.perf_counter() - fetch_dates_start

            # Sort by INTERNALDATE
            sorted_uids = sorted(uid_dates.items(), key=lambda x: x[1], reverse=(order == "desc"))

            # Paginate
            start = (page - 1) * page_size
            page_uids = [uid for uid, _ in sorted_uids[start : start + page_size]]

            if not page_uids:
                logger.info(f"Phase 1 (dates): {len(uid_dates)} UIDs in {fetch_dates_elapsed:.2f}s, page {page} empty")
                return

            # Phase 2: Batch fetch headers for requested page only
            fetch_headers_start = time.perf_counter()
            metadata_by_uid = await self._batch_fetch_headers(imap, page_uids)
            fetch_headers_elapsed = time.perf_counter() - fetch_headers_start

            logger.info(
                f"Fetched page {page}: {fetch_dates_elapsed:.2f}s dates ({len(uid_dates)} UIDs), "
                f"{fetch_headers_elapsed:.2f}s headers ({len(page_uids)} UIDs)"
            )

            # Yield in sorted order
            for uid in page_uids:
                if uid in metadata_by_uid:
                    yield metadata_by_uid[uid]
        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

    def _check_email_content(self, data: list) -> bool:
        """Check if the fetched data contains actual email content."""
        for item in data:
            if isinstance(item, bytes) and b"FETCH (" in item and b"RFC822" not in item and b"BODY" not in item:
                # This is just metadata, not actual content
                continue
            elif isinstance(item, bytes | bytearray) and len(item) > 100:
                # This looks like email content
                return True
        return False

    def _extract_raw_email(self, data: list) -> bytes | None:
        """Extract raw email bytes from IMAP response data."""
        # The email content is typically at index 1 as a bytearray
        if len(data) > 1 and isinstance(data[1], bytearray):
            return bytes(data[1])

        # Search through all items for email content
        for item in data:
            if isinstance(item, bytes | bytearray) and len(item) > 100:
                # Skip IMAP protocol responses
                if isinstance(item, bytes) and b"FETCH" in item:
                    continue
                # This is likely the email content
                return bytes(item) if isinstance(item, bytearray) else item
        return None

    async def _fetch_email_with_formats(self, imap, email_id: str) -> list | None:
        """Try different fetch formats to get email data."""
        fetch_formats = ["RFC822", "BODY[]", "BODY.PEEK[]", "(BODY.PEEK[])"]

        for fetch_format in fetch_formats:
            try:
                _, data = await imap.uid("fetch", email_id, fetch_format)

                if data and len(data) > 0 and self._check_email_content(data):
                    return data

            except Exception as e:
                logger.debug(f"Fetch format {fetch_format} failed: {e}")

        return None

    async def get_email_body_by_id(self, email_id: str, mailbox: str = "INBOX", truncate_body: int | None = None) -> dict[str, Any] | None:
        imap = self.imap_class(self.email_server.host, self.email_server.port)
        try:
            # Wait for the connection to be established
            await imap._client_task
            await imap.wait_hello_from_server()

            # Login and select inbox
            await imap.login(self.email_server.user_name, self.email_server.password)
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))

            # Fetch the specific email by UID
            data = await self._fetch_email_with_formats(imap, email_id)
            if not data:
                logger.error(f"Failed to fetch UID {email_id} with any format")
                return None

            # Extract raw email data
            raw_email = self._extract_raw_email(data)
            if not raw_email:
                logger.error(f"Could not find email data in response for email ID: {email_id}")
                return None

            # Parse the email
            try:
                return self._parse_email_data(raw_email, email_id, truncate_body)
            except Exception as e:
                logger.error(f"Error parsing email: {e!s}")
                return None

        finally:
            # Ensure we logout properly
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

    async def download_attachment(
        self,
        email_id: str,
        attachment_name: str,
        save_path: str,
        mailbox: str = "INBOX",
    ) -> dict[str, Any]:
        """Download a specific attachment from an email and save it to disk.

        Args:
            email_id: The UID of the email containing the attachment.
            attachment_name: The filename of the attachment to download.
            save_path: The local path where the attachment will be saved.
            mailbox: The mailbox to search in (default: "INBOX").

        Returns:
            A dictionary with download result information.
        """
        imap = self.imap_class(self.email_server.host, self.email_server.port)
        try:
            await imap._client_task
            await imap.wait_hello_from_server()

            await imap.login(self.email_server.user_name, self.email_server.password)
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))

            data = await self._fetch_email_with_formats(imap, email_id)
            if not data:
                msg = f"Failed to fetch email with UID {email_id}"
                logger.error(msg)
                raise ValueError(msg)

            raw_email = self._extract_raw_email(data)
            if not raw_email:
                msg = f"Could not find email data for email ID: {email_id}"
                logger.error(msg)
                raise ValueError(msg)

            parser = BytesParser(policy=default)
            email_message = parser.parsebytes(raw_email)

            # Find the attachment
            attachment_data = None
            mime_type = None

            if email_message.is_multipart():
                for part in email_message.walk():
                    content_disposition = str(part.get("Content-Disposition", ""))
                    if "attachment" in content_disposition:
                        filename = part.get_filename()
                        if filename == attachment_name:
                            attachment_data = part.get_payload(decode=True)
                            mime_type = part.get_content_type()
                            break

            if attachment_data is None:
                msg = f"Attachment '{attachment_name}' not found in email {email_id}"
                logger.error(msg)
                raise ValueError(msg)

            # Save to disk
            save_file = Path(save_path)
            save_file.parent.mkdir(parents=True, exist_ok=True)
            save_file.write_bytes(attachment_data)

            logger.info(f"Attachment '{attachment_name}' saved to {save_path}")

            return {
                "email_id": email_id,
                "attachment_name": attachment_name,
                "mime_type": mime_type or "application/octet-stream",
                "size": len(attachment_data),
                "saved_path": str(save_file.resolve()),
            }

        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

    def _validate_attachment(self, file_path: str) -> Path:
        """Validate attachment file path."""
        path = Path(file_path)
        if not path.exists():
            msg = f"Attachment file not found: {file_path}"
            logger.error(msg)
            raise FileNotFoundError(msg)

        if not path.is_file():
            msg = f"Attachment path is not a file: {file_path}"
            logger.error(msg)
            raise ValueError(msg)

        return path

    def _create_attachment_part(self, path: Path) -> MIMEApplication:
        """Create MIME attachment part from file."""
        with open(path, "rb") as f:
            file_data = f.read()

        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        attachment_part = MIMEApplication(file_data, _subtype=mime_type.split("/")[1])
        attachment_part.add_header(
            "Content-Disposition",
            "attachment",
            filename=path.name,
        )
        logger.info(f"Attached file: {path.name} ({mime_type})")
        return attachment_part

    def _create_message_with_attachments(self, body: str, html: bool, attachments: list[str]) -> MIMEMultipart:
        """Create multipart message with attachments."""
        msg = MIMEMultipart()
        content_type = "html" if html else "plain"
        text_part = MIMEText(body, content_type, "utf-8")
        msg.attach(text_part)

        for file_path in attachments:
            try:
                path = self._validate_attachment(file_path)
                attachment_part = self._create_attachment_part(path)
                msg.attach(attachment_part)
            except Exception as e:
                logger.error(f"Failed to attach file {file_path}: {e}")
                raise

        return msg

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
    ):
        # Create message with or without attachments
        if attachments:
            msg = self._create_message_with_attachments(body, html, attachments)
        else:
            content_type = "html" if html else "plain"
            msg = MIMEText(body, content_type, "utf-8")

        # Handle subject with special characters
        if any(ord(c) > 127 for c in subject):
            msg["Subject"] = Header(subject, "utf-8")
        else:
            msg["Subject"] = subject

        # Handle sender name with special characters
        if any(ord(c) > 127 for c in self.sender):
            msg["From"] = Header(self.sender, "utf-8")
        else:
            msg["From"] = self.sender

        msg["To"] = ", ".join(recipients)

        # Add CC header if provided (visible to recipients)
        if cc:
            msg["Cc"] = ", ".join(cc)

        # Set threading headers for replies
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        # Set Date and Message-Id headers so the same values appear in both
        # the SMTP-sent copy and the IMAP Sent folder copy
        msg["Date"] = email.utils.formatdate(localtime=True)
        sender_domain = self.sender.rsplit("@", 1)[-1].rstrip(">")
        msg["Message-Id"] = email.utils.make_msgid(domain=sender_domain)

        # Note: BCC recipients are not added to headers (they remain hidden)
        # but will be included in the actual recipients for SMTP delivery

        async with aiosmtplib.SMTP(
            hostname=self.email_server.host,
            port=self.email_server.port,
            start_tls=self.smtp_start_tls,
            use_tls=self.smtp_use_tls,
            tls_context=self._get_smtp_ssl_context(),
        ) as smtp:
            await smtp.login(self.email_server.user_name, self.email_server.password)

            # Create a combined list of all recipients for delivery
            all_recipients = recipients.copy()
            if cc:
                all_recipients.extend(cc)
            if bcc:
                all_recipients.extend(bcc)

            await smtp.send_message(msg, recipients=all_recipients)

        # Return the message for potential saving to Sent folder
        return msg

    async def _find_sent_folder_by_flag(self, imap) -> str | None:
        """Find the Sent folder by searching for the \\Sent IMAP flag.

        Args:
            imap: Connected IMAP client

        Returns:
            The folder name with the \\Sent flag, or None if not found
        """
        try:
            # List all folders - aioimaplib requires reference_name and mailbox_pattern
            _, folders = await imap.list('""', "*")

            # Search for folder with \Sent flag
            for folder in folders:
                folder_str = folder.decode("utf-8") if isinstance(folder, bytes) else str(folder)
                # IMAP LIST response format: (flags) "delimiter" "name"
                # Example: (\Sent \HasNoChildren) "/" "Gesendete Objekte"
                if r"\Sent" in folder_str or "\\Sent" in folder_str:
                    # Extract folder name from the response
                    # Split by quotes and get the last quoted part
                    parts = folder_str.split('"')
                    if len(parts) >= 3:
                        folder_name = parts[-2]  # The folder name is the second-to-last quoted part
                        logger.info(f"Found Sent folder by \\Sent flag: '{folder_name}'")
                        return folder_name
        except Exception as e:
            logger.debug(f"Error finding Sent folder by flag: {e}")

        return None

    async def append_to_sent(
        self,
        msg: MIMEText | MIMEMultipart,
        incoming_server: EmailServer,
        sent_folder_name: str | None = None,
    ) -> bool:
        """Append a sent message to the IMAP Sent folder.

        Args:
            msg: The email message that was sent
            incoming_server: IMAP server configuration for accessing Sent folder
            sent_folder_name: Override folder name, or None for auto-detection

        Returns:
            True if successfully saved, False otherwise
        """
        imap_class = aioimaplib.IMAP4_SSL if incoming_server.use_ssl else aioimaplib.IMAP4
        imap = imap_class(incoming_server.host, incoming_server.port)

        # Common Sent folder names across different providers
        sent_folder_candidates = [
            sent_folder_name,  # User-specified override (if provided)
            "Sent",
            "INBOX.Sent",
            "Sent Items",
            "Sent Mail",
            "[Gmail]/Sent Mail",
            "INBOX/Sent",
        ]
        # Filter out None values
        sent_folder_candidates = [f for f in sent_folder_candidates if f]

        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await imap.login(incoming_server.user_name, incoming_server.password)
            await _send_imap_id(imap)

            # Try to find Sent folder by IMAP \Sent flag first
            flag_folder = await self._find_sent_folder_by_flag(imap)
            if flag_folder and flag_folder not in sent_folder_candidates:
                # Add it at the beginning (high priority)
                sent_folder_candidates.insert(0, flag_folder)

            # Try to find and use the Sent folder
            for folder in sent_folder_candidates:
                try:
                    logger.debug(f"Trying Sent folder: '{folder}'")
                    # Try to select the folder to verify it exists
                    result = await imap.select(_quote_mailbox(folder))
                    logger.debug(f"Select result for '{folder}': {result}")

                    # aioimaplib returns (status, data) where status is a string like 'OK' or 'NO'
                    status = result[0] if isinstance(result, tuple) else result
                    if str(status).upper() == "OK":
                        # Folder exists, append the message
                        msg_bytes = msg.as_bytes()
                        logger.debug(f"Appending message to '{folder}'")
                        # aioimaplib.append signature: (message_bytes, mailbox, flags, date)
                        append_result = await imap.append(
                            msg_bytes,
                            mailbox=_quote_mailbox(folder),
                            flags=r"(\Seen)",
                        )
                        logger.debug(f"Append result: {append_result}")
                        append_status = append_result[0] if isinstance(append_result, tuple) else append_result
                        if str(append_status).upper() == "OK":
                            logger.info(f"Saved sent email to '{folder}'")
                            return True
                        else:
                            logger.warning(f"Failed to append to '{folder}': {append_status}")
                    else:
                        logger.debug(f"Folder '{folder}' select returned: {status}")
                except Exception as e:
                    logger.debug(f"Folder '{folder}' not available: {e}")
                    continue

            logger.warning("Could not find a valid Sent folder to save the message")
            return False

        except Exception as e:
            logger.error(f"Error saving to Sent folder: {e}")
            return False
        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.debug(f"Error during logout: {e}")

    async def delete_emails(self, email_ids: list[str], mailbox: str = "INBOX") -> tuple[list[str], list[str]]:
        """Delete emails by their UIDs. Returns (deleted_ids, failed_ids)."""
        imap = self.imap_class(self.email_server.host, self.email_server.port)
        deleted_ids = []
        failed_ids = []

        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await imap.login(self.email_server.user_name, self.email_server.password)
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))

            for email_id in email_ids:
                try:
                    await imap.uid("store", email_id, "+FLAGS", r"(\Deleted)")
                    deleted_ids.append(email_id)
                except Exception as e:
                    logger.error(f"Failed to delete email {email_id}: {e}")
                    failed_ids.append(email_id)

            await imap.expunge()
        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

        return deleted_ids, failed_ids

    async def create_folder_if_needed(self, imap, folder_name: str) -> bool:
        """Create a folder if it doesn't exist, using an existing IMAP connection."""
        try:
            _, existing = await imap.list('""', _quote_mailbox(folder_name))
            if existing and any(isinstance(item, bytes) and len(item) > 2 for item in existing):
                return True

            await imap.create(_quote_mailbox(folder_name))
            logger.info(f"Created folder: {folder_name}")
            return True
        except Exception as e:
            logger.error(f"Error creating folder {folder_name}: {e}")
            return False

    async def move_emails_to_folder(
        self,
        email_ids: list[str],
        target_folder: str,
        source_mailbox: str = "INBOX",
        create_if_missing: bool = True,
    ) -> dict[str, list[str]]:
        """Move emails to a target folder. Returns dict with 'moved' and 'failed' lists."""
        moved = []
        failed = []

        imap = self.imap_class(self.email_server.host, self.email_server.port)
        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await imap.login(self.email_server.user_name, self.email_server.password)
            await _send_imap_id(imap)

            if create_if_missing and not await self.create_folder_if_needed(imap, target_folder):
                    logger.error(f"Failed to create folder: {target_folder}")
                    return {"moved": [], "failed": email_ids}

            await imap.select(_quote_mailbox(source_mailbox))

            for email_id in email_ids:
                try:
                    # Try MOVE command first (RFC 6851)
                    try:
                        response = await imap.uid("move", email_id, _quote_mailbox(target_folder))
                        if response and response.result == "OK":
                            logger.info(f"Moved email {email_id} to {target_folder} using MOVE")
                            moved.append(email_id)
                            continue
                    except Exception as move_error:
                        logger.debug(f"MOVE not supported, falling back to COPY+DELETE: {move_error}")

                    # Fallback to COPY + DELETE
                    copy_response = await imap.uid("copy", email_id, _quote_mailbox(target_folder))
                    if copy_response and copy_response.result == "OK":
                        await imap.uid("store", email_id, "+FLAGS", r"(\Deleted)")
                        logger.info(f"Moved email {email_id} to {target_folder} using COPY+DELETE")
                        moved.append(email_id)
                    else:
                        logger.error(f"Failed to copy email {email_id}: {copy_response}")
                        failed.append(email_id)

                except Exception as e:
                    logger.error(f"Error moving email {email_id} to {target_folder}: {e}")
                    failed.append(email_id)

            # Expunge deleted messages after all moves
            if moved:
                await imap.expunge()

        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

        return {"moved": moved, "failed": failed}


def _normalize_flags(flags: list[str]) -> list[str]:
    """Normalize flag format - ensure proper backslash prefix for system flags."""
    normalized = []
    for flag in flags:
        # Handle both bytes and strings from IMAP responses
        if isinstance(flag, bytes):
            flag = flag.decode("utf-8", errors="replace")
        clean_flag = flag.strip().lstrip("\\")
        normalized.append(f"\\{clean_flag}")
    return normalized


def _build_store_command(operation: str, silent: bool) -> str:
    """Build STORE command based on operation and silent flag."""
    commands = {
        "add": "+FLAGS.SILENT" if silent else "+FLAGS",
        "remove": "-FLAGS.SILENT" if silent else "-FLAGS",
        "replace": "FLAGS.SILENT" if silent else "FLAGS",
    }
    if operation not in commands:
        raise ValueError(f"Invalid operation: {operation}")
    return commands[operation]


class ClassicEmailHandler(EmailHandler):
    def __init__(self, email_settings: EmailSettings):
        self.email_settings = email_settings
        self.incoming_client = EmailClient(email_settings.incoming)
        self.outgoing_client = EmailClient(
            email_settings.outgoing,
            sender=f"{email_settings.full_name} <{email_settings.email_address}>",
        )
        self.save_to_sent = email_settings.save_to_sent
        self.sent_folder_name = email_settings.sent_folder_name

    @asynccontextmanager
    async def imap_connection(self, select_mailbox: str = "INBOX"):
        """Reusable IMAP connection context manager for the incoming server."""
        client = self.incoming_client
        imap = client.imap_class(client.email_server.host, client.email_server.port)
        try:
            await imap._client_task
            await imap.wait_hello_from_server()

            await imap.login(client.email_server.user_name, client.email_server.password)
            await _send_imap_id(imap)

            if select_mailbox:
                logger.debug(f"Selecting IMAP mailbox: {select_mailbox}")
                select_result = await imap.select(_quote_mailbox(select_mailbox))

                if hasattr(select_result, "result") and select_result.result == "OK":
                    logger.debug(f"Successfully selected mailbox: {select_mailbox}")
                else:
                    logger.error(f"Failed to select mailbox {select_mailbox}: {select_result}")
                    raise ValueError(f"Failed to select mailbox {select_mailbox}")

            yield imap

        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

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
    ) -> EmailMetadataPageResponse:
        emails = []
        async for email_data in self.incoming_client.get_emails_metadata_stream(
            page,
            page_size,
            before,
            since,
            subject,
            from_address,
            to_address,
            order,
            mailbox,
            seen,
            flagged,
            answered,
        ):
            emails.append(EmailMetadata.from_email(email_data))
        total = await self.incoming_client.get_email_count(
            before,
            since,
            subject,
            from_address=from_address,
            to_address=to_address,
            mailbox=mailbox,
            seen=seen,
            flagged=flagged,
            answered=answered,
        )
        return EmailMetadataPageResponse(
            page=page,
            page_size=page_size,
            before=before,
            since=since,
            subject=subject,
            emails=emails,
            total=total,
        )

    async def get_emails_content(
        self, email_ids: list[str], mailbox: str = "INBOX", truncate_body: int | None = None
    ) -> EmailContentBatchResponse:
        """Batch retrieve email body content"""
        emails = []
        failed_ids = []

        for email_id in email_ids:
            try:
                email_data = await self.incoming_client.get_email_body_by_id(email_id, mailbox, truncate_body)
                if email_data:
                    emails.append(
                        EmailBodyResponse(
                            email_id=email_data["email_id"],
                            message_id=email_data.get("message_id"),
                            subject=email_data["subject"],
                            sender=email_data["from"],
                            recipients=email_data["to"],
                            date=email_data["date"],
                            body=email_data["body"],
                            attachments=email_data["attachments"],
                        )
                    )
                else:
                    failed_ids.append(email_id)
            except Exception as e:
                logger.error(f"Failed to retrieve email {email_id}: {e}")
                failed_ids.append(email_id)

        return EmailContentBatchResponse(
            emails=emails,
            requested_count=len(email_ids),
            retrieved_count=len(emails),
            failed_ids=failed_ids,
        )

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
        msg = await self.outgoing_client.send_email(
            recipients, subject, body, cc, bcc, html, attachments, in_reply_to, references
        )

        # Save to Sent folder if enabled
        if self.save_to_sent and msg:
            try:
                await self.outgoing_client.append_to_sent(
                    msg,
                    self.email_settings.incoming,
                    self.sent_folder_name,
                )
            except Exception as e:
                logger.error(f"Failed to save email to Sent folder: {e}", exc_info=True)

    async def delete_emails(self, email_ids: list[str], mailbox: str = "INBOX") -> tuple[list[str], list[str]]:
        """Delete emails by their UIDs. Returns (deleted_ids, failed_ids)."""
        return await self.incoming_client.delete_emails(email_ids, mailbox)

    async def list_folders(self, pattern: str = "*") -> list[dict[str, Any]]:
        """List all IMAP folders with details."""
        async with self.imap_connection(select_mailbox=None) as imap:
            _, folders = await imap.list('""', pattern)
            folder_info = []

            for folder in folders:
                if isinstance(folder, bytes):
                    folder_str = folder.decode("utf-8")
                    # Parse IMAP LIST response: (\Flags) "delimiter" "folder name"
                    match = re.match(r'\(([^)]*)\)\s+"([^"]+)"\s+"([^"]+)"', folder_str)
                    if match:
                        flags, delimiter, name = match.groups()
                        folder_info.append({
                            "name": name,
                            "delimiter": delimiter,
                            "flags": flags.split() if flags else [],
                            "can_select": "\\Noselect" not in flags,
                        })

            return folder_info

    async def move_emails_to_folder(
        self,
        email_ids: list[str],
        target_folder: str,
        source_mailbox: str = "INBOX",
        create_if_missing: bool = True,
    ) -> dict[str, list[str]]:
        """Move one or more emails to a specified folder."""
        return await self.incoming_client.move_emails_to_folder(
            email_ids, target_folder, source_mailbox, create_if_missing
        )

    async def download_attachment(
        self,
        email_id: str,
        attachment_name: str,
        save_path: str,
        mailbox: str = "INBOX",
    ) -> AttachmentDownloadResponse:
        """Download an email attachment and save it to the specified path.

        Args:
            email_id: The UID of the email containing the attachment.
            attachment_name: The filename of the attachment to download.
            save_path: The local path where the attachment will be saved.
            mailbox: The mailbox to search in (default: "INBOX").

        Returns:
            AttachmentDownloadResponse with download result information.
        """
        result = await self.incoming_client.download_attachment(email_id, attachment_name, save_path, mailbox)
        return AttachmentDownloadResponse(
            email_id=result["email_id"],
            attachment_name=result["attachment_name"],
            mime_type=result["mime_type"],
            size=result["size"],
            saved_path=result["saved_path"],
        )

    async def _execute_batch_flag_operation(
        self, imap, uid_list: str, store_cmd: str, flags_str: str, email_ids: list[str], operation: str
    ) -> dict[str, bool]:
        """Execute batch flag operation and return results."""
        results = {}
        response = await imap.uid("store", uid_list, store_cmd, flags_str)

        if response and response.result == "OK":
            for eid in email_ids:
                results[str(eid)] = True
            logger.info(f"Successfully {operation} flags {flags_str} on {len(email_ids)} emails")
        else:
            for eid in email_ids:
                results[str(eid)] = False
            logger.error(f"Failed to {operation} flags {flags_str}: {response}")

        return results

    async def _execute_individual_flag_operations(
        self, imap, email_ids: list[str], store_cmd: str, flags_str: str, operation: str
    ) -> dict[str, bool]:
        """Execute individual flag operations as fallback."""
        results = {}
        for eid in email_ids:
            try:
                response = await imap.uid("store", str(eid), store_cmd, flags_str)
                results[str(eid)] = response and response.result == "OK"
                if not results[str(eid)]:
                    logger.error(f"Failed to {operation} flags on UID {eid}: {response}")
            except Exception as individual_error:
                logger.error(f"Error modifying flags on UID {eid}: {individual_error}")
                results[str(eid)] = False
        return results

    async def _modify_flags(
        self, email_ids: list[str], flags: list[str], operation: str, silent: bool = False
    ) -> dict[str, bool]:
        """Core flag modification method using IMAP STORE command."""
        if not email_ids or not flags:
            return {}

        normalized_flags = _normalize_flags(flags)
        flags_str = f"({' '.join(normalized_flags)})"
        store_cmd = _build_store_command(operation, silent)
        uid_list = ",".join(str(eid) for eid in email_ids)

        try:
            async with self.imap_connection() as imap:
                try:
                    return await self._execute_batch_flag_operation(
                        imap, uid_list, store_cmd, flags_str, email_ids, operation
                    )
                except Exception as e:
                    logger.warning(f"Batch flag operation failed, trying individual operations: {e}")
                    return await self._execute_individual_flag_operations(
                        imap, email_ids, store_cmd, flags_str, operation
                    )

        except Exception as e:
            logger.error(f"Error in flag modification: {e}")
            return {str(eid): False for eid in email_ids}

    async def add_flags(self, email_ids: list[str], flags: list[str], silent: bool = False) -> dict[str, bool]:
        """Add flags to emails using +FLAGS operation."""
        return await self._modify_flags(email_ids, flags, "add", silent)

    async def remove_flags(self, email_ids: list[str], flags: list[str], silent: bool = False) -> dict[str, bool]:
        """Remove flags from emails using -FLAGS operation."""
        return await self._modify_flags(email_ids, flags, "remove", silent)

    async def replace_flags(self, email_ids: list[str], flags: list[str], silent: bool = False) -> dict[str, bool]:
        """Replace all flags on emails using FLAGS operation."""
        return await self._modify_flags(email_ids, flags, "replace", silent)

    async def save_email_to_file(  # noqa: C901
        self,
        email_id: str,
        file_path: str,
        output_format: str = "markdown",
        include_headers: bool = True,
        mailbox: str = "INBOX",
    ) -> SaveEmailToFileResponse:
        """Save a complete email to a file without truncation.

        Args:
            email_id: The UID of the email to save.
            file_path: The file path where to save the email content.
            output_format: 'html' for original HTML, 'markdown' to convert HTML to markdown.
            include_headers: Include email headers (subject, from, date, etc.).
            mailbox: The mailbox to search in (default: "INBOX").
        """
        client = self.incoming_client
        imap = client.imap_class(client.email_server.host, client.email_server.port)
        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await imap.login(client.email_server.user_name, client.email_server.password)
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))

            data = await client._fetch_email_with_formats(imap, email_id)
            if not data:
                msg = f"Failed to fetch email with UID {email_id}"
                logger.error(msg)
                raise ValueError(msg)

            raw_email = client._extract_raw_email(data)
            if not raw_email:
                msg = f"Could not find email data for email ID: {email_id}"
                logger.error(msg)
                raise ValueError(msg)

            # Parse the email to extract parts
            parser = BytesParser(policy=default)
            email_message = parser.parsebytes(raw_email)

            subject = email_message.get("Subject", "")
            sender = email_message.get("From", "")
            date_str = email_message.get("Date", "")

            # Extract body content preserving original format
            text_body = ""
            html_body = ""

            if email_message.is_multipart():
                for part in email_message.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))
                    if "attachment" in content_disposition:
                        continue
                    if content_type == "text/plain" and not text_body:
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset("utf-8")
                            try:
                                text_body = payload.decode(charset)
                            except UnicodeDecodeError:
                                text_body = payload.decode("utf-8", errors="replace")
                    elif content_type == "text/html" and not html_body:
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset("utf-8")
                            try:
                                html_body = payload.decode(charset)
                            except UnicodeDecodeError:
                                html_body = payload.decode("utf-8", errors="replace")
            else:
                content_type = email_message.get_content_type()
                payload = email_message.get_payload(decode=True)
                if payload:
                    charset = email_message.get_content_charset("utf-8")
                    try:
                        decoded = payload.decode(charset)
                    except UnicodeDecodeError:
                        decoded = payload.decode("utf-8", errors="replace")
                    if content_type == "text/html":
                        html_body = decoded
                    else:
                        text_body = decoded

            # Determine output content based on format
            if output_format == "html":
                body = html_body or text_body
            else:
                # markdown: convert HTML if available, otherwise use plain text
                body = html_to_markdown(html_body) if html_body else text_body

            # Build file content
            parts = []
            if include_headers:
                parts.append(f"Subject: {subject}")
                parts.append(f"From: {sender}")
                parts.append(f"Date: {date_str}")
                parts.append(f"Email-ID: {email_id}")
                parts.append("")
                parts.append("---")
                parts.append("")

            parts.append(body)
            content = "\n".join(parts)

            # Write to file
            save_path = Path(file_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(content, encoding="utf-8")

            return SaveEmailToFileResponse(
                email_id=email_id,
                file_path=str(save_path.resolve()),
                content_length=len(content),
                output_format=output_format,
            )

        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")
