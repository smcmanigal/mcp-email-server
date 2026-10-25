import asyncio
import base64
import email.utils
import itertools
import mimetypes
import re
import ssl
import time
import unicodedata
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email import encoders
from email.header import Header
from email.headerregistry import Address
from email.mime.base import MIMEBase
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


_IMAP_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _imap_search_date(value: datetime) -> str:
    """Format a SEARCH date as DD-MON-YYYY (RFC 3501) with English month names.

    strftime("%b") follows LC_TIME, so a non-English locale would produce month names
    the server rejects.
    """
    return f"{value.day:02d}-{_IMAP_MONTHS[value.month - 1]}-{value.year:04d}"


_LIST_LITERAL_SUFFIX = re.compile(rb"\{(\d+)\}\s*$")


def _read_list_string(text: str, pos: int) -> tuple[str | None, int]:
    """Read one astring/nstring from a LIST line at pos: "quoted", NIL or an atom.

    Returns (value, position after it); value is None for NIL.
    """
    while pos < len(text) and text[pos] == " ":
        pos += 1
    if pos < len(text) and text[pos] == '"':
        value = []
        pos += 1
        while pos < len(text) and text[pos] != '"':
            if text[pos] == "\\" and pos + 1 < len(text):
                pos += 1
            value.append(text[pos])
            pos += 1
        return "".join(value), pos + 1
    end = pos
    while end < len(text) and text[end] != " ":
        end += 1
    token = text[pos:end]
    return (None if token.upper() == "NIL" else token), end


def _parse_list_line(line: str) -> tuple[list[str], str | None, str] | None:
    """Parse one LIST response line '(flags) delimiter name' into (flags, delimiter, name).

    Handles a quoted or NIL delimiter and a quoted (with \\" and \\\\ escapes) or atom
    name. The name is returned as sent by the server (still modified UTF-7).
    """
    line = line.strip()
    if line.upper().startswith("LIST "):
        line = line[5:].lstrip()
    if not line.startswith("("):
        return None
    close = line.find(")")
    if close == -1:
        return None
    flags = line[1:close].split()
    delimiter, pos = _read_list_string(line, close + 1)
    name, _ = _read_list_string(line, pos)
    if not name:
        return None
    return flags, delimiter, name


def _iter_list_entries(lines: list) -> list[tuple[list[str], str | None, str]]:
    """Parse the lines of a LIST response, joining names the server sent as literals.

    aioimaplib delivers '(flags) "/" {n}' followed by the n-byte name as a separate item.
    """
    entries = []
    items = [
        item.encode() if isinstance(item, str) else bytes(item) if isinstance(item, bytearray) else item
        for item in lines
    ]
    i = 0
    while i < len(items):
        item = items[i]
        i += 1
        if not isinstance(item, bytes):
            continue
        literal = _LIST_LITERAL_SUFFIX.search(item)
        if literal and i < len(items) and isinstance(items[i], bytes):
            name = items[i][: int(literal.group(1))]
            i += 1
            head = item[: literal.start()].decode("utf-8", errors="replace")
            name_text = name.decode("utf-8", errors="replace").replace("\\", "\\\\").replace('"', '\\"')
            item_text = f'{head}"{name_text}"'
        else:
            item_text = item.decode("utf-8", errors="replace")
        parsed = _parse_list_line(item_text)
        if parsed:
            entries.append(parsed)
    return entries


def _encode_modified_utf7(name: str) -> str:
    """Encode a mailbox name as RFC 3501 modified UTF-7 (section 5.1.3).

    Printable ASCII stands for itself except "&", which becomes "&-"; any other run of
    characters becomes "&" + base64 of its UTF-16BE (with "," for "/", no padding) + "-".
    """
    out = []
    run = []

    def flush():
        if run:
            encoded = base64.b64encode("".join(run).encode("utf-16-be")).decode("ascii")
            out.append("&" + encoded.rstrip("=").replace("/", ",") + "-")
            run.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            run.append(ch)
    flush()
    return "".join(out)


def _decode_modified_utf7(name: str) -> str:
    """Decode an RFC 3501 modified UTF-7 mailbox name; raises ValueError if it is malformed."""
    out = []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        end = name.find("-", i + 1)
        if end == -1:
            raise ValueError(f"Unterminated modified UTF-7 sequence in {name!r}")
        chunk = name[i + 1 : end]
        if not chunk:
            out.append("&")
        else:
            if not re.fullmatch(r"[A-Za-z0-9+,]+", chunk):
                raise ValueError(f"Invalid modified UTF-7 sequence in {name!r}")
            data = chunk.replace(",", "/")
            raw = base64.b64decode(data + "=" * (-len(data) % 4))
            out.append(raw.decode("utf-16-be"))
        i = end + 1
    return "".join(out)


def _decode_mailbox_name(name: str) -> str:
    """Decode a mailbox name from the server for display; returns it unchanged if not valid mUTF-7."""
    if "&" not in name:
        return name
    try:
        return _decode_modified_utf7(name)
    except (ValueError, UnicodeDecodeError):
        return name


def _encode_mailbox_name(name: str) -> str:
    """Encode a mailbox name for the wire, leaving an already-encoded name alone.

    Callers may pass a decoded name ("Änderungen") or, from older configs and rules, the
    encoded form ("&AMQ-nderungen"). An ASCII name that is canonical modified UTF-7 is
    sent as is so it is not double-encoded; a literal "&" (e.g. "Tom & Jerry") is encoded.
    """
    if name.isascii():
        if "&" not in name:
            return name
        try:
            if _encode_modified_utf7(_decode_modified_utf7(name)) == name:
                return name
        except (ValueError, UnicodeDecodeError):
            pass
    return _encode_modified_utf7(name)


def _as_utc(value: datetime) -> datetime:
    """A naive datetime is taken as UTC (as the tools document); aware ones are converted."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _needs_exact_time_filter(value: datetime | None) -> bool:
    """Whether a since/before boundary is finer than the whole UTC day IMAP SEARCH works in."""
    if value is None:
        return False
    return _as_utc(value).time() != datetime.min.time()


_INTERNAL_DATE = re.compile(r"\s*(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})\s*")


def _parse_internal_date(value: str) -> datetime | None:
    """Parse an IMAP INTERNALDATE ("17-Jul-1996 02:44:25 -0700") without strptime's locale-dependent %b."""
    match = _INTERNAL_DATE.fullmatch(value)
    if not match or match.group(2).upper() not in _IMAP_MONTHS:
        return None
    day, mon, year, hour, minute, second, sign, off_h, off_m = match.groups()
    offset = timedelta(hours=int(off_h), minutes=int(off_m)) * (-1 if sign == "-" else 1)
    return datetime(
        int(year),
        _IMAP_MONTHS.index(mon.upper()) + 1,
        int(day),
        int(hour),
        int(minute),
        int(second),
        tzinfo=timezone(offset),
    )


def _reject_unquotable(value: str, what: str) -> None:
    """Raise if value cannot appear in an IMAP quoted string (RFC 3501: no CR, LF or NUL).

    Quoting does not neutralise a line break: a CR/LF in a search value or mailbox name
    from the caller would end the command and start a new one on the connection.
    """
    if any(ch in value for ch in "\r\n\0"):
        raise ValueError(f"{what} must not contain line breaks or NUL characters: {value!r}")


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
    _reject_unquotable(mailbox, "Mailbox name")
    escaped = _encode_mailbox_name(mailbox).replace("\\", "\\\\").replace('"', r"\"")
    return f'"{escaped}"'


def _quote_search_param(param: str) -> str:
    """Quote and escape IMAP search parameter per RFC 3501 Section 9."""
    _reject_unquotable(param, "Search value")
    escaped = param.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


_FETCH_BODY_LITERAL = re.compile(rb"FETCH \(.*\b(?:RFC822|BODY\[\])\s*\{(\d+)\}\s*$", re.IGNORECASE)
_SEARCH_COMPLETED_ONLY = re.compile(r"SEARCH completed \(took [0-9]+ ms\)", re.IGNORECASE)


def _parse_search_uids(messages: list | None) -> list[str]:
    """Return the UIDs from a UID SEARCH response.

    Compliant servers put the space-separated UIDs on the first response line,
    which is empty when nothing matches. iCloud omits that line for an empty
    result, so aioimaplib's first line is the completion text instead
    ("SEARCH completed (took 5 ms)"). That is read as an empty result. The line
    is only trusted when every token is a UID: the "5" in "took 5 ms" must never
    be acted on as a message. Any other unexpected text is logged and treated
    as no matches.
    """
    if not messages or not messages[0]:
        return []
    first = messages[0]
    text = bytes(first).decode(errors="replace") if isinstance(first, bytes | bytearray) else str(first)
    tokens = text.split()
    if all(token.isascii() and token.isdigit() for token in tokens):
        return tokens
    if _SEARCH_COMPLETED_ONLY.fullmatch(text.strip()):
        logger.debug("Server omitted the empty UID SEARCH payload; treating the search as empty")
    else:
        logger.warning(f"Unexpected UID SEARCH response, treating as no matches: {text!r}")
    return []


def _unfold_header(value) -> str | None:
    """A header value with folding whitespace collapsed to single spaces, or None if absent."""
    if value is None:
        return None
    unfolded = " ".join(str(value).split())
    return unfolded or None


def _decode_text_part(payload: bytes, charset: str) -> str:
    """Decode a text part with its declared charset, falling back to UTF-8 with replacement.

    An unknown charset label (e.g. charset=x-bogus) raises LookupError rather than
    UnicodeDecodeError; both fall back so one bad part can't make the message unreadable.
    """
    try:
        return payload.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _is_attached_message(part) -> bool:
    """Whether a part is an encapsulated email (message/rfc822), e.g. a forward-as-attachment."""
    return part.get_content_type() == "message/rfc822"


def _walk_message_parts(message):
    """Like Message.walk(), but an attached message/rfc822 is yielded as one part, not entered.

    walk() descends into encapsulated messages, so their text leaked into the outer body.
    """
    yield message
    if _is_attached_message(message) or not message.is_multipart():
        return
    for sub_part in message.get_payload():
        yield from _walk_message_parts(sub_part)


def _attachment_filename(part) -> str | None:
    """The name an attachment is listed and downloaded under.

    An attached email often has no filename; name it after its subject (or a fixed
    fallback) with a .eml extension so it can be listed and downloaded.
    """
    filename = part.get_filename()
    if filename or not _is_attached_message(part):
        return filename
    inner = part.get_payload()
    subject = str(inner[0].get("Subject", "")) if isinstance(inner, list) and inner else ""
    stem = re.sub(r"[^\w .()-]+", "_", subject).strip(" ._")[:80]
    return f"{stem or 'attached-message'}.eml"


def _attachment_payload(part) -> bytes | None:
    """Decoded bytes of an attachment part; an attached email is returned as its raw .eml bytes."""
    if _is_attached_message(part):
        inner = part.get_payload()
        return inner[0].as_bytes() if isinstance(inner, list) and inner else None
    return part.get_payload(decode=True)


def _is_attachment_part(part) -> bool:
    """Whether a MIME part is listed as an attachment.

    That is a part with a filename and an attachment, inline or missing disposition (some
    senders give the name only in Content-Type), or an attached email (message/rfc822).
    Download matches any part with a filename, so listing must not be narrower.

    Applies to a single-part message's own top-level part too, such as a scanner
    email whose whole body is one PDF.
    """
    if _is_attached_message(part):
        return True
    if not part.get_filename():
        return False
    content_disposition = str(part.get("Content-Disposition", "")).strip().lower()
    return not content_disposition or "attachment" in content_disposition or "inline" in content_disposition


_IMAP_ID_TIMEOUT = 10


async def _send_imap_id(imap: aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL) -> None:
    """Send the RFC 2971 ID command, only to servers that advertise ID.

    Some servers require ID before SELECT (e.g. 163.com). We send the compact
    '("name" "value" ...)' form directly: aioimaplib's id() emits '( "name" ... )', which
    strict servers reject. Servers without ID can answer with an untagged BAD that never
    completes the command (e.g. ukr.net), stalling the connection until the IMAP timeout,
    so the command is skipped when ID is not advertised and is bounded by a short timeout.
    Call after _imap_authenticate, which refreshes the capability list.

    See: https://github.com/ai-zerolab/mcp-email-server/issues/85 and #217
    """
    if not _imap_has_capability(imap, "ID"):
        logger.debug("IMAP server does not advertise ID; skipping ID command")
        return
    try:
        response = await imap.protocol.execute(
            aioimaplib.Command(
                "ID",
                imap.protocol.new_tag(),
                '("name" "mcp-email-server" "version" "1.0.0")',
                loop=imap.protocol.loop,
                timeout=_IMAP_ID_TIMEOUT,
            )
        )
        if getattr(response, "result", "OK") != "OK":
            logger.warning(f"IMAP ID command returned {response.result}: {response.lines}")
    except Exception as e:
        logger.warning(f"IMAP ID command failed: {type(e).__name__}: {e!s}")


def _create_ssl_context(verify_ssl: bool) -> ssl.SSLContext | None:
    """Create SSL context for SMTP/IMAP connections.

    Returns None for default verification, or permissive context
    for self-signed certificates when verify_ssl=False.
    """
    if verify_ssl:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# Backwards-compatible alias
_create_smtp_ssl_context = _create_ssl_context


def _build_xoauth2_string(user: str, access_token: str) -> str:
    """Build XOAUTH2 SASL authentication string."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"


async def _imap_authenticate(
    imap,
    email_server: EmailServer,
    auth_type: str = "password",
    email_address: str = "",
    oauth2_provider: str | None = None,
    oauth2_client_id: str | None = None,
    oauth2_tenant_id: str | None = None,
    oauth2_client_secret: str | None = None,
) -> None:
    """Authenticate to IMAP server using password or OAuth2."""
    if auth_type == "oauth2":
        from mcp_email_server.oauth2 import get_token_manager

        manager = get_token_manager(
            provider=oauth2_provider,
            client_id=oauth2_client_id,
            tenant_id=oauth2_tenant_id or "common",
            client_secret=oauth2_client_secret,
        )
        access_token = manager.get_access_token(email_address)
        # aioimaplib.xoauth2(user, token) builds the SASL string internally.
        # Despite the outer type hint saying bytes, the protocol implementation
        # expects token as str (used in f-string + passed as scrub to send()).
        response = await imap.xoauth2(email_address, access_token)
        if hasattr(response, "result") and response.result != "OK":
            raise RuntimeError(f"IMAP XOAUTH2 authentication failed: {response}")
    else:
        # aioimaplib's login() returns a Response with .result of "OK"/"NO"/"BAD"
        # but never raises on NO/BAD. Unchecked, the caller proceeds to issue
        # SELECT/FETCH on a NONAUTH connection, producing the misleading error
        # "command SELECT illegal in state NONAUTH" — and retries re-attempt
        # LOGIN on fresh connections, amplifying lock-outs on servers that
        # rate-limit failed logins (e.g. Proton Mail Bridge).
        response = await imap.login(email_server.user_name, email_server.password.get_secret_value())
        if response.result != "OK":
            detail = " ".join(
                line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
                for line in (response.lines or [])
            ).strip()
            raise ConnectionError(
                f"IMAP login failed for {email_server.user_name!r}: {response.result}"
                + (f" ({detail})" if detail else "")
            )

    await _refresh_imap_capabilities(imap)


async def _refresh_imap_capabilities(imap) -> None:
    """Re-read CAPABILITY now that the session is authenticated.

    aioimaplib only knows the pre-auth greeting's capabilities (plus any CAPABILITY in a
    LOGIN reply; XOAUTH2 merges none). Servers like Gmail advertise MOVE and UIDPLUS only
    after authentication, so without this every move fell back to COPY + \\Deleted.
    """
    try:
        await imap.protocol.capability()
    except Exception as e:
        logger.warning(f"IMAP CAPABILITY refresh after login failed, using pre-auth capabilities: {e!s}")


async def _select_or_raise(imap, mailbox: str):
    """SELECT mailbox and raise a readable error if the server refuses it.

    aioimaplib returns NO/BAD instead of raising. Unchecked, a mistyped or missing mailbox
    left the session in AUTH state and the next command failed with the misleading
    "command UID illegal in state AUTH".
    """
    response = await imap.select(_quote_mailbox(mailbox))
    result = getattr(response, "result", None)
    if isinstance(result, str) and result != "OK":
        detail = " ".join(
            line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
            for line in (getattr(response, "lines", None) or [])
        ).strip()
        raise ValueError(f"Mailbox {mailbox!r} could not be selected: {result}" + (f" ({detail})" if detail else ""))
    return response


def _imap_has_capability(imap, capability: str) -> bool:
    try:
        return capability in imap.protocol.capabilities
    except Exception:
        return False


async def _expunge_uids(imap, uids: list[str]) -> None:
    """Permanently remove only the given UIDs (which the caller marked \\Deleted).

    A bare EXPUNGE would also remove messages another client marked \\Deleted in the same
    mailbox, so UID EXPUNGE (RFC 4315 UIDPLUS) is used when available. Without UIDPLUS
    we fall back to the bare EXPUNGE with a warning rather than leaving the copies behind.
    """
    if not uids:
        return
    if _imap_has_capability(imap, "UIDPLUS"):
        uid_set = EmailClient._build_uid_set(sorted(uids, key=int))
        response = await imap.uid("expunge", uid_set)
        if getattr(response, "result", "OK") != "OK":
            logger.warning(f"UID EXPUNGE {uid_set} returned {response.result}: {response.lines}")
        return
    logger.warning(
        "Server lacks UIDPLUS; using a mailbox-wide EXPUNGE, which also removes messages "
        "other clients marked \\Deleted in this mailbox"
    )
    await imap.expunge()


async def _smtp_authenticate(
    smtp,
    email_server: EmailServer,
    auth_type: str = "password",
    email_address: str = "",
    oauth2_provider: str | None = None,
    oauth2_client_id: str | None = None,
    oauth2_tenant_id: str | None = None,
    oauth2_client_secret: str | None = None,
) -> None:
    """Authenticate to SMTP server using password or OAuth2."""
    if auth_type == "oauth2":
        from mcp_email_server.oauth2 import get_token_manager

        manager = get_token_manager(
            provider=oauth2_provider,
            client_id=oauth2_client_id,
            tenant_id=oauth2_tenant_id or "common",
            client_secret=oauth2_client_secret,
        )
        access_token = manager.get_access_token(email_address)
        auth_string = _build_xoauth2_string(email_address, access_token)
        # Base64-encode the SASL string; execute_command expects bytes args
        auth_b64 = base64.b64encode(auth_string.encode())
        # EHLO must precede the raw AUTH command. After STARTTLS the ESMTP
        # session is reset, and execute_command() (unlike smtp.login()) does
        # not auto-EHLO, so the server otherwise rejects AUTH with
        # "503 send hello first". That status is returned, not raised, so we
        # also check the AUTH response here — failures must surface at the
        # auth layer rather than later as a generic "530 Client not
        # authenticated" at send time.
        await smtp.ehlo()
        code, message = await smtp.execute_command(b"AUTH", b"XOAUTH2", auth_b64)
        if code != 235:
            raise RuntimeError(f"SMTP XOAUTH2 authentication failed: {code} {message}")
    else:
        await smtp.login(email_server.user_name, email_server.password.get_secret_value())


class EmailClient:
    def __init__(
        self,
        email_server: EmailServer,
        sender: str | None = None,
        auth_type: str = "password",
        email_address: str = "",
        oauth2_provider: str | None = None,
        oauth2_client_id: str | None = None,
        oauth2_tenant_id: str | None = None,
        oauth2_client_secret: str | None = None,
        search_chunk_size: int = 10,
        sender_name: str | None = None,
    ):
        self.email_server = email_server
        # With sender_name, sender is the bare address; without it, sender may be a
        # preformatted "Name <address>" string (legacy callers).
        self.sender = sender or email_server.user_name
        self.sender_name = sender_name
        self.auth_type = auth_type
        self.email_address = email_address
        self.oauth2_provider = oauth2_provider
        self.oauth2_client_id = oauth2_client_id
        self.oauth2_tenant_id = oauth2_tenant_id
        self.oauth2_client_secret = oauth2_client_secret
        self.search_chunk_size = search_chunk_size

        self.imap_class = aioimaplib.IMAP4_SSL if self.email_server.use_ssl else aioimaplib.IMAP4

        self.smtp_use_tls = self.email_server.use_ssl
        self.smtp_start_tls = self.email_server.start_ssl
        self.smtp_verify_ssl = self.email_server.verify_ssl

    IMAP_TIMEOUT = 30.0

    def _imap_connect(self) -> aioimaplib.IMAP4_SSL | aioimaplib.IMAP4:
        """Create a new IMAP connection with the configured SSL context."""
        if self.email_server.use_ssl:
            imap_ssl_context = _create_ssl_context(self.email_server.verify_ssl)
            return self.imap_class(
                self.email_server.host, self.email_server.port, ssl_context=imap_ssl_context, timeout=self.IMAP_TIMEOUT
            )
        return self.imap_class(self.email_server.host, self.email_server.port, timeout=self.IMAP_TIMEOUT)

    def _get_smtp_ssl_context(self) -> ssl.SSLContext | None:
        """Get SSL context for SMTP connections based on verify_ssl setting."""
        return _create_ssl_context(self.smtp_verify_ssl)

    @staticmethod
    def _parse_recipients(email_message) -> list[str]:
        """Extract recipient addresses from To and Cc headers.

        Parsed as RFC 5322 address lists, so a quoted display name containing a comma
        ("Smith, John" <j@x.com>) stays one recipient.
        """
        recipients = []
        for header in ("To", "Cc"):
            value = email_message.get(header, "")
            if not value:
                continue
            addresses = [(name, addr) for name, addr in email.utils.getaddresses([str(value)]) if addr]
            if not addresses:
                # Unparseable header: keep the old best-effort split rather than drop it.
                recipients.extend(part.strip() for part in str(value).split(",") if part.strip())
                continue
            for name, addr in addresses:
                try:
                    recipients.append(str(Address(display_name=name, addr_spec=addr)))
                except (ValueError, IndexError, TypeError):
                    recipients.append(f"{name} <{addr}>" if name else addr)
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

    def _parse_email_data(  # noqa: C901
        self, raw_email: bytes, email_id: str | None = None, truncate_body: int | None = None
    ) -> dict[str, Any]:
        """Parse raw email data into a structured dictionary."""
        parser = BytesParser(policy=default)
        email_message = parser.parsebytes(raw_email)

        # Extract email parts
        subject = email_message.get("Subject", "")
        sender = email_message.get("From", "")
        date_str = email_message.get("Date", "")

        # Extract Message-ID for reply threading
        message_id = _unfold_header(email_message.get("Message-ID"))
        # The parent's threading headers, so a reply can build In-Reply-To and References
        in_reply_to = _unfold_header(email_message.get("In-Reply-To"))
        references = _unfold_header(email_message.get("References"))

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
            for part in _walk_message_parts(email_message):
                content_type = part.get_content_type()

                # Handle attachments (explicit attachments, inline parts with filenames, attached emails)
                if _is_attachment_part(part):
                    attachments.append(_attachment_filename(part))
                # Handle text parts - prefer text/plain
                elif content_type == "text/plain":
                    body_part = part.get_payload(decode=True)
                    if body_part:
                        charset = part.get_content_charset("utf-8")
                        body += _decode_text_part(body_part, charset)
                # Collect HTML as fallback
                elif content_type == "text/html" and not body:
                    html_part = part.get_payload(decode=True)
                    if html_part:
                        charset = part.get_content_charset("utf-8")
                        html_body += _decode_text_part(html_part, charset)

            # Fall back to HTML if no plain text found
            if not body and html_body:
                body = _strip_html(html_body)
        elif _is_attachment_part(email_message):
            # The whole message is one attached file (e.g. a scanned PDF); list it
            # as an attachment rather than decoding its bytes as the body.
            attachments.append(email_message.get_filename())
        else:
            # Handle single-part emails
            content_type = email_message.get_content_type()
            payload = email_message.get_payload(decode=True)
            if payload:
                charset = email_message.get_content_charset("utf-8")
                text = _decode_text_part(payload, charset)

                body = _strip_html(text) if content_type == "text/html" else text
        limit = truncate_body if truncate_body is not None else MAX_BODY_LENGTH
        if body and len(body) > limit:
            body = body[:limit] + "...[TRUNCATED]"
        return {
            "email_id": email_id or "",
            "message_id": message_id,
            "in_reply_to": in_reply_to,
            "references": references,
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
            search_criteria.extend(["BEFORE", _imap_search_date(before)])
        if since:
            search_criteria.extend(["SINCE", _imap_search_date(since)])
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
            # Expose Message-ID for reply threading / de-duplication (full-body parse
            # already sets this in _parse_email_data; mirror it on the header path).
            # Unfolded: Outlook folds a long Message-ID onto a continuation line, which
            # would otherwise come back with a leading tab.
            message_id = _unfold_header(email_message.get("Message-ID"))

            to_addresses = self._parse_recipients(email_message)
            date = self._parse_date(date_str)

            return {
                "email_id": email_id,
                "message_id": message_id,
                "subject": subject,
                "from": sender,
                "to": to_addresses,
                "date": date,
                "attachments": [],
            }
        except Exception as e:
            logger.error(f"Error parsing email headers: {type(e).__name__}: {e!r}")
            return None

    async def _fetch_dates_chunk(
        self,
        imap: aioimaplib.IMAP4_SSL | aioimaplib.IMAP4,
        chunk: list[bytes],
        chunk_num: int,
        total_chunks: int,
        timeout: float = 30.0,
    ) -> dict[str, datetime]:
        """Fetch INTERNALDATE for a single chunk of UIDs."""
        uid_list = ",".join(uid.decode() for uid in chunk)
        chunk_start = time.perf_counter()
        _, data = await asyncio.wait_for(
            imap.uid("fetch", uid_list, "(INTERNALDATE)"),
            timeout=timeout,
        )
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
                parsed = _parse_internal_date(date_str)
                if parsed is not None:
                    chunk_dates[uid] = parsed

        if total_chunks > 1:
            logger.info(f"Fetched dates chunk {chunk_num}/{total_chunks}: {len(chunk)} UIDs in {chunk_elapsed:.2f}s")

        return chunk_dates

    async def _filter_uids_by_internal_date(
        self, imap, uids: list[str], since: datetime | None, before: datetime | None
    ) -> list[str]:
        """Keep the UIDs whose INTERNALDATE satisfies since <= date < before (exact to the second)."""
        dates = await self._batch_fetch_dates(imap, [uid.encode() for uid in uids])
        since_utc = _as_utc(since) if since else None
        before_utc = _as_utc(before) if before else None
        kept = [
            uid
            for uid in uids
            if uid in dates
            and (since_utc is None or dates[uid] >= since_utc)
            and (before_utc is None or dates[uid] < before_utc)
        ]
        logger.info(f"Exact time filter kept {len(kept)} of {len(uids)} candidates")
        return kept

    async def _batch_fetch_dates(
        self,
        imap: aioimaplib.IMAP4_SSL | aioimaplib.IMAP4,
        email_ids: list[bytes],
        chunk_size: int = 500,
    ) -> dict[str, datetime]:
        """Batch fetch INTERNALDATE for all UIDs in sequential chunks.

        Uses a conservative chunk_size (default 500) to avoid hitting
        Python's recursion limit in aioimaplib's recursive response parser
        (see: aioimaplib _handle_responses). IMAP connections are sequential
        by protocol (RFC 9051 §5.5), so chunks must be fetched serially —
        not in parallel.
        """
        if not email_ids:
            return {}

        # Split into chunks
        chunks = [email_ids[i : i + chunk_size] for i in range(0, len(email_ids), chunk_size)]
        total_chunks = len(chunks)

        # Fetch chunks sequentially (IMAP protocol is sequential on a single connection)
        uid_dates: dict[str, datetime] = {}
        for chunk_num, chunk in enumerate(chunks, 1):
            chunk_dates = await self._fetch_dates_chunk(imap, chunk, chunk_num, total_chunks)
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

    async def get_emails_metadata_page(
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
        body: str | None = None,
        text: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search once and return (metadata for the requested page, total matches).

        The total comes from the same UID SEARCH, so listing needs one connection and
        one login, and the page and its total cannot disagree.
        """
        imap = self._imap_connect()
        try:
            # Wait for the connection to be established
            await imap._client_task
            await imap.wait_hello_from_server()

            # Login and select mailbox
            await _imap_authenticate(
                imap,
                self.email_server,
                self.auth_type,
                self.email_address,
                self.oauth2_provider,
                self.oauth2_client_id,
                self.oauth2_tenant_id,
                self.oauth2_client_secret,
            )
            await _send_imap_id(imap)
            await _select_or_raise(imap, mailbox)

            # IMAP SINCE/BEFORE compare whole days of the server's INTERNALDATE, so a
            # boundary with a time of day is widened by a day here and enforced exactly
            # below against each candidate's INTERNALDATE.
            exact_since = _needs_exact_time_filter(since)
            exact_before = _needs_exact_time_filter(before)
            search_criteria = self._build_search_criteria(
                before + timedelta(days=1) if exact_before else before,
                since - timedelta(days=1) if exact_since else since,
                subject,
                body=body,
                text=text,
                from_address=from_address,
                to_address=to_address,
                seen=seen,
                flagged=flagged,
                answered=answered,
            )
            logger.info(f"Get metadata: Search criteria: {search_criteria}")

            # Search for messages - use UID SEARCH for better compatibility
            # Use charset=None to avoid CHARSET utf-8 which M365 rejects
            result, messages = await imap.uid_search(*search_criteria, charset=None)

            # Handle errors or empty responses
            if result != "OK":
                logger.error(f"SEARCH failed: {result} {messages}")
                return [], 0
            email_ids = _parse_search_uids(messages)
            if not email_ids:
                logger.warning("No messages returned from search")
                return [], 0

            logger.info(f"Found {len(email_ids)} email IDs")

            if exact_since or exact_before:
                email_ids = await self._filter_uids_by_internal_date(
                    imap, email_ids, since if exact_since else None, before if exact_before else None
                )
                if not email_ids:
                    return [], 0

            # Sort by UID (monotonically increasing, approximates chronological order)
            sorted_uids = sorted(email_ids, key=lambda uid: int(uid), reverse=(order == "desc"))

            # Paginate
            start = (page - 1) * page_size
            page_uids = [
                uid.decode() if isinstance(uid, bytes) else uid for uid in sorted_uids[start : start + page_size]
            ]

            if not page_uids:
                logger.info(f"Page {page} empty (total {len(email_ids)} UIDs)")
                return [], len(email_ids)

            # Batch fetch headers for requested page only
            fetch_headers_start = time.perf_counter()
            metadata_by_uid = await self._batch_fetch_headers(imap, page_uids)
            fetch_headers_elapsed = time.perf_counter() - fetch_headers_start

            logger.info(
                f"Fetched page {page}: {len(page_uids)} of {len(email_ids)} UIDs, "
                f"headers in {fetch_headers_elapsed:.2f}s"
            )

            # Return in sorted order
            return [metadata_by_uid[uid] for uid in page_uids if uid in metadata_by_uid], len(email_ids)
        finally:
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
        body: str | None = None,
        text: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield the metadata of one page; see get_emails_metadata_page."""
        emails, _total = await self.get_emails_metadata_page(
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
            body=body,
            text=text,
        )
        for email_data in emails:
            yield email_data

    @staticmethod
    def _find_literal_payload(data: list) -> bytes | None:
        """Return the message literal that follows a 'FETCH (... BODY[] {n}' line, if any.

        The {n} length identifies the payload exactly, so short messages are found too;
        the size heuristics below remain as a fallback for servers that reply differently.
        """
        for item, payload in itertools.pairwise(data):
            if not isinstance(item, bytes | bytearray) or not isinstance(payload, bytes | bytearray):
                continue
            match = _FETCH_BODY_LITERAL.search(bytes(item))
            if match and int(match.group(1)) > 0 and len(payload) == int(match.group(1)):
                return bytes(payload)
        return None

    def _check_email_content(self, data: list) -> bool:
        """Check if the fetched data contains actual email content."""
        if self._find_literal_payload(data) is not None:
            return True
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
        literal = self._find_literal_payload(data)
        if literal is not None:
            return literal

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
        """Try different fetch formats to get email data.

        PEEK forms come first because RFC822 and BODY[] set \\Seen (RFC 3501), so reading
        would mark the message read. They remain as fallbacks for servers that reject PEEK.
        """
        fetch_formats = ["BODY.PEEK[]", "(BODY.PEEK[])", "BODY[]", "RFC822"]

        for fetch_format in fetch_formats:
            try:
                _, data = await imap.uid("fetch", email_id, fetch_format)

                if data and len(data) > 0 and self._check_email_content(data):
                    return data

            except Exception as e:
                logger.debug(f"Fetch format {fetch_format} failed: {e}")

        return None

    async def get_email_body_by_id(
        self, email_id: str, mailbox: str = "INBOX", truncate_body: int | None = None
    ) -> dict[str, Any] | None:
        imap = self._imap_connect()
        try:
            # Wait for the connection to be established
            await imap._client_task
            await imap.wait_hello_from_server()

            # Login and select inbox
            await _imap_authenticate(
                imap,
                self.email_server,
                self.auth_type,
                self.email_address,
                self.oauth2_provider,
                self.oauth2_client_id,
                self.oauth2_tenant_id,
                self.oauth2_client_secret,
            )
            await _send_imap_id(imap)
            await _select_or_raise(imap, mailbox)

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
                logger.error(f"Error parsing email: {type(e).__name__}: {e!r}")
                return None

        finally:
            # Ensure we logout properly
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

    @staticmethod
    def _normalize_attachment_name(name: str) -> str:
        """Normalize attachment filenames for robust MIME round-trip matching.

        Unicode allows two byte representations of accented characters: NFC
        (single codepoint) and NFD (base char + combining mark). macOS mail
        clients typically emit NFD in MIME headers while keyboards and most
        other systems produce NFC — the strings display identically but fail
        an exact ``==`` comparison.
        """
        return unicodedata.normalize("NFC", name)

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
        imap = self._imap_connect()
        try:
            await imap._client_task
            await imap.wait_hello_from_server()

            await _imap_authenticate(
                imap,
                self.email_server,
                self.auth_type,
                self.email_address,
                self.oauth2_provider,
                self.oauth2_client_id,
                self.oauth2_tenant_id,
                self.oauth2_client_secret,
            )
            await _send_imap_id(imap)
            await _select_or_raise(imap, mailbox)

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
            normalized_attachment_name = self._normalize_attachment_name(attachment_name)

            # walk() yields the message itself first, so a single-part message whose
            # whole body is the attachment is matched too. Multipart containers have
            # no payload of their own to save.
            for part in _walk_message_parts(email_message):
                if part.is_multipart() and not _is_attached_message(part):
                    continue
                filename = _attachment_filename(part)
                if not isinstance(filename, str):
                    continue
                if self._normalize_attachment_name(filename) == normalized_attachment_name:
                    attachment_data = _attachment_payload(part)
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

    def _create_attachment_part(self, path: Path) -> MIMEBase:
        """Create MIME attachment part from file."""
        with open(path, "rb") as f:
            file_data = f.read()

        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        # Keep the guessed main type (image/png, text/plain); MIMEApplication forced
        # application/* and produced types like application/png. Base64 keeps bytes exact.
        maintype, subtype = mime_type.split("/", 1)
        attachment_part = MIMEBase(maintype, subtype)
        attachment_part.set_payload(file_data)
        encoders.encode_base64(attachment_part)
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
                logger.error(f"Failed to attach file {file_path}: {type(e).__name__}: {e!r}")
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

        # Build From from name and address separately: formataddr quotes names with
        # specials like commas and RFC 2047-encodes only a non-ASCII name, never the address.
        if self.sender_name is not None:
            envelope_sender = self.sender
            msg["From"] = email.utils.formataddr((self.sender_name, self.sender), charset="utf-8")
        else:
            envelope_sender = email.utils.parseaddr(self.sender)[1] or self.sender
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
        sender_domain = envelope_sender.rsplit("@", 1)[-1]
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
            await _smtp_authenticate(
                smtp,
                self.email_server,
                self.auth_type,
                self.email_address,
                self.oauth2_provider,
                self.oauth2_client_id,
                self.oauth2_tenant_id,
                self.oauth2_client_secret,
            )

            # Create a combined list of all recipients for delivery
            all_recipients = recipients.copy()
            if cc:
                all_recipients.extend(cc)
            if bcc:
                all_recipients.extend(bcc)

            # Pass the envelope sender explicitly; otherwise aiosmtplib parses it from
            # the From header, which yields "Smith" for a display name like "Smith, John".
            await smtp.send_message(msg, sender=envelope_sender, recipients=all_recipients)

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

            # Search for folder with \Sent flag; names may be quoted or atoms,
            # e.g. (\Sent \HasNoChildren) "/" "Gesendete Objekte" or (\Sent) "/" Sent
            for flags, _delimiter, folder_name in _iter_list_entries(folders):
                if any(flag.lower() == "\\sent" for flag in flags):
                    folder_name = _decode_mailbox_name(folder_name)
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
        auth_type: str = "password",
        email_address: str = "",
        oauth2_provider: str | None = None,
        oauth2_client_id: str | None = None,
        oauth2_tenant_id: str | None = None,
        oauth2_client_secret: str | None = None,
    ) -> bool:
        """Append a sent message to the IMAP Sent folder.

        Args:
            msg: The email message that was sent
            incoming_server: IMAP server configuration for accessing Sent folder
            sent_folder_name: Override folder name, or None for auto-detection

        Returns:
            True if successfully saved, False otherwise
        """
        if incoming_server.use_ssl:
            imap_ssl_context = _create_ssl_context(incoming_server.verify_ssl)
            imap = aioimaplib.IMAP4_SSL(
                incoming_server.host,
                incoming_server.port,
                ssl_context=imap_ssl_context,
                timeout=EmailClient.IMAP_TIMEOUT,
            )
        else:
            imap = aioimaplib.IMAP4(incoming_server.host, incoming_server.port, timeout=EmailClient.IMAP_TIMEOUT)

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
            await _imap_authenticate(
                imap,
                incoming_server,
                auth_type,
                email_address,
                oauth2_provider,
                oauth2_client_id,
                oauth2_tenant_id,
                oauth2_client_secret,
            )
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
                        # IMAP literals must use CRLF line endings (RFC 3501); the default
                        # compat32 serialization emits bare LF.
                        msg_bytes = msg.as_bytes(policy=msg.policy.clone(linesep="\r\n"))
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

        except ConnectionError:
            # Login failures must reach the caller (send_email logs them with
            # the real cause) rather than hide behind a generic save error.
            raise
        except Exception as e:
            logger.error(f"Error saving to Sent folder: {type(e).__name__}: {e!r}")
            return False
        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.debug(f"Error during logout: {e}")

    async def delete_emails(self, email_ids: list[str], mailbox: str = "INBOX") -> tuple[list[str], list[str]]:
        """Delete emails by their UIDs. Returns (deleted_ids, failed_ids)."""
        imap = self._imap_connect()
        deleted_ids = []
        failed_ids = []

        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await _imap_authenticate(
                imap,
                self.email_server,
                self.auth_type,
                self.email_address,
                self.oauth2_provider,
                self.oauth2_client_id,
                self.oauth2_tenant_id,
                self.oauth2_client_secret,
            )
            await _send_imap_id(imap)
            await _select_or_raise(imap, mailbox)

            for email_id in email_ids:
                try:
                    # aioimaplib returns NO/BAD rather than raising, so check the result.
                    response = await imap.uid("store", email_id, "+FLAGS", r"(\Deleted)")
                    if getattr(response, "result", "OK") != "OK":
                        logger.error(f"Failed to delete email {email_id}: STORE returned {response.result}")
                        failed_ids.append(email_id)
                        continue
                    deleted_ids.append(email_id)
                except Exception as e:
                    logger.error(f"Failed to delete email {email_id}: {type(e).__name__}: {e!r}")
                    failed_ids.append(email_id)

            await _expunge_uids(imap, deleted_ids)
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
            # A \Noselect or \NonExistent entry is only a hierarchy placeholder (e.g. "Projects"
            # when only "Projects/2026" exists); it cannot hold mail, so CREATE it instead.
            if any(
                not any(flag.lower() in ("\\noselect", "\\nonexistent") for flag in flags)
                for flags, _delimiter, _name in _iter_list_entries(existing or [])
            ):
                return True

            result = await imap.create(_quote_mailbox(folder_name))
            if result.result != "OK":
                logger.error(f"IMAP CREATE failed for {folder_name}: {result.lines}")
                return False
            logger.info(f"Created folder: {folder_name}")
            return True
        except Exception as e:
            logger.error(f"Error creating folder {folder_name}: {type(e).__name__}: {e!r}")
            return False

    async def create_folder(self, folder_name: str) -> bool:
        """Create a folder, managing its own IMAP connection."""
        imap = self._imap_connect()
        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await _imap_authenticate(
                imap,
                self.email_server,
                self.auth_type,
                self.email_address,
                self.oauth2_provider,
                self.oauth2_client_id,
                self.oauth2_tenant_id,
                self.oauth2_client_secret,
            )
            await _send_imap_id(imap)
            return await self.create_folder_if_needed(imap, folder_name)
        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

    async def move_emails_to_folder(
        self,
        email_ids: list[str],
        target_folder: str,
        source_mailbox: str = "INBOX",
        create_if_missing: bool = True,
    ) -> dict[str, list[str]]:
        """Move emails to a target folder. Returns dict with 'moved' and 'failed' lists."""
        imap = self._imap_connect()
        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await _imap_authenticate(
                imap,
                self.email_server,
                self.auth_type,
                self.email_address,
                self.oauth2_provider,
                self.oauth2_client_id,
                self.oauth2_tenant_id,
                self.oauth2_client_secret,
            )
            await _send_imap_id(imap)

            if create_if_missing and not await self.create_folder_if_needed(imap, target_folder):
                logger.error(f"Failed to create folder: {target_folder}")
                return {"moved": [], "failed": email_ids}

            await _select_or_raise(imap, source_mailbox)
            moved, failed = await self._move_uids(imap, email_ids, target_folder)
            return {"moved": moved, "failed": failed}

        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

    @staticmethod
    def _build_or_criteria(field: str, values: list[str]) -> list[str]:
        """Build a single IMAP OR tree for multiple field criteria.

        IMAP OR is prefix notation with exactly two operands:
          1 value:  FIELD "a"
          2 values: OR FIELD "a" FIELD "b"
          3 values: OR FIELD "a" OR FIELD "b" FIELD "c"
        """
        parts = [[field, _quote_search_param(v)] for v in values]
        result = parts[-1]
        for part in reversed(parts[:-1]):
            result = ["OR", *part, *result]
        return result

    async def _search_by_field(
        self, imap, field: str, values: list[str], since: datetime | None, chunk_size: int | None = None
    ) -> list[str]:
        """Search for emails matching field values using batched OR queries, returning sorted UIDs."""
        if not values:
            return []

        if chunk_size is None:
            chunk_size = self.search_chunk_size

        date_criteria = []
        if since:
            date_criteria = ["SINCE", _imap_search_date(since)]

        all_uids: set[str] = set()
        for i in range(0, len(values), chunk_size):
            chunk = values[i : i + chunk_size]
            or_criteria = self._build_or_criteria(field, chunk)
            search_criteria = date_criteria + or_criteria
            result, messages = await imap.uid_search(*search_criteria, charset=None)
            if result != "OK":
                logger.warning(
                    f"IMAP search returned {result!r} for {field} chunk of {len(chunk)} values "
                    f"(server response: {messages!r}). Try lowering search_chunk_size for this account."
                )
                continue
            all_uids.update(_parse_search_uids(messages))
        return sorted(all_uids, key=lambda uid: int(uid))

    async def _move_uids(self, imap, email_ids: list[str], target_folder: str) -> tuple[list[str], list[str]]:
        """Move a list of UIDs to target_folder. Returns (moved, failed)."""
        moved = []
        failed = []
        flagged_deleted = []
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
                    store_response = await imap.uid("store", email_id, "+FLAGS", r"(\Deleted)")
                    if getattr(store_response, "result", "OK") != "OK":
                        # Copied but not removed from the source; report it rather than claim a move.
                        logger.error(
                            f"Copied email {email_id} to {target_folder} but could not mark it deleted: "
                            f"STORE returned {store_response.result}"
                        )
                        failed.append(email_id)
                        continue
                    logger.info(f"Moved email {email_id} to {target_folder} using COPY+DELETE")
                    moved.append(email_id)
                    flagged_deleted.append(email_id)
                else:
                    logger.error(f"Failed to copy email {email_id}: {copy_response}")
                    failed.append(email_id)
            except Exception as e:
                logger.error(f"Error moving email {email_id} to {target_folder}: {type(e).__name__}: {e!r}")
                failed.append(email_id)

        # Native MOVE already removed its messages; only the COPY fallback needs an expunge.
        await _expunge_uids(imap, flagged_deleted)
        return moved, failed

    @staticmethod
    def _build_uid_set(uids: list[str]) -> str:
        """Build an IMAP UID set string with range compression.

        Consecutive UIDs are compressed into ranges: [1,2,3,5,7,8,9] -> "1:3,5,7:9"
        UIDs must be pre-sorted in ascending numeric order.
        """
        if not uids:
            return ""
        nums = [int(u) for u in uids]
        ranges = []
        start = end = nums[0]
        for n in nums[1:]:
            if n == end + 1:
                end = n
            else:
                ranges.append(f"{start}:{end}" if end > start else str(start))
                start = end = n
        ranges.append(f"{start}:{end}" if end > start else str(start))
        return ",".join(ranges)

    async def _batch_move_uids(
        self, imap, email_ids: list[str], target_folder: str, batch_size: int = 80, mark_read: bool = False
    ) -> tuple[list[str], list[str]]:
        """Move UIDs in batches using UID set notation. Fails fast on first error."""
        moved = []
        failed = []
        flagged_deleted = []
        quoted_folder = _quote_mailbox(target_folder)

        for i in range(0, len(email_ids), batch_size):
            batch = email_ids[i : i + batch_size]
            uid_set = self._build_uid_set(batch)

            try:
                if mark_read:
                    await imap.uid("store", uid_set, "+FLAGS", r"(\Seen)")

                # Try MOVE command first (RFC 6851)
                try:
                    response = await imap.uid("move", uid_set, quoted_folder)
                    if response and response.result == "OK":
                        logger.info(f"Moved {len(batch)} emails to {target_folder} using MOVE (UIDs: {uid_set})")
                        moved.extend(batch)
                        continue
                except Exception as move_error:
                    logger.debug(f"MOVE not supported, falling back to COPY+DELETE: {move_error}")

                # Fallback to COPY + DELETE
                copy_response = await imap.uid("copy", uid_set, quoted_folder)
                if copy_response and copy_response.result == "OK":
                    store_response = await imap.uid("store", uid_set, "+FLAGS", r"(\Deleted)")
                    if getattr(store_response, "result", "OK") != "OK":
                        logger.error(
                            f"Copied batch to {target_folder} but could not mark it deleted: "
                            f"STORE returned {store_response.result} (UIDs: {uid_set})"
                        )
                        failed.extend(batch)
                        failed.extend(email_ids[i + batch_size :])
                        break
                    logger.info(f"Moved {len(batch)} emails to {target_folder} using COPY+DELETE (UIDs: {uid_set})")
                    moved.extend(batch)
                    flagged_deleted.extend(batch)
                else:
                    logger.error(f"Failed to copy batch to {target_folder}: {copy_response}")
                    failed.extend(batch)
                    failed.extend(email_ids[i + batch_size :])
                    break
            except Exception as e:
                logger.error(f"Error moving batch to {target_folder}: {type(e).__name__}: {e!r}")
                failed.extend(batch)
                failed.extend(email_ids[i + batch_size :])
                break

        # Native MOVE already removed its messages; only the COPY fallback needs an expunge.
        await _expunge_uids(imap, flagged_deleted)
        return moved, failed

    async def apply_filter_rule(
        self,
        senders: list[str] | None = None,
        subjects: list[str] | None = None,
        target_folder: str = "",
        source_mailbox: str = "INBOX",
        since: datetime | None = None,
        dry_run: bool = False,
        limit: int | None = None,
        mark_read: bool = False,
    ) -> dict[str, list[str]]:
        """Search for emails matching criteria and move them to a target folder."""
        if limit is not None and limit < 1:
            raise ValueError("limit must be a positive integer")
        if not senders and not subjects:
            raise ValueError("Either senders or subjects must be provided")

        imap = self._imap_connect()
        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await _imap_authenticate(
                imap,
                self.email_server,
                self.auth_type,
                self.email_address,
                self.oauth2_provider,
                self.oauth2_client_id,
                self.oauth2_tenant_id,
                self.oauth2_client_secret,
            )
            await _send_imap_id(imap)
            await _select_or_raise(imap, source_mailbox)

            if senders and subjects:
                from_uids = await self._search_by_field(imap, "FROM", senders, since)
                subject_uids = await self._search_by_field(imap, "SUBJECT", subjects, since)
                matched = sorted(set(from_uids) & set(subject_uids), key=lambda uid: int(uid))
            elif senders:
                matched = await self._search_by_field(imap, "FROM", senders, since)
            else:
                matched = await self._search_by_field(imap, "SUBJECT", subjects, since)
            to_process = matched
            if limit is not None and len(matched) > limit:
                to_process = matched[:limit]

            if dry_run or not to_process:
                return {"matched": matched, "moved": [], "failed": []}

            if not await self.create_folder_if_needed(imap, target_folder):
                logger.error(f"Failed to create folder: {target_folder}")
                return {"matched": matched, "moved": [], "failed": to_process}

            moved, failed = await self._batch_move_uids(imap, to_process, target_folder, mark_read=mark_read)
            return {"matched": matched, "moved": moved, "failed": failed}

        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")


# RFC 3501 system flags a client may store (\\Recent is server-managed).
_SYSTEM_FLAGS = {name.lower(): f"\\{name}" for name in ("Seen", "Answered", "Flagged", "Deleted", "Draft")}


# flag = "\\" atom / atom (RFC 3501): printable ASCII except atom-specials ( ) { SP % * " \\ ]
_VALID_IMAP_FLAG = re.compile(r"\\?[!#$&\'+-\[^-z|}~]+")


def _validate_flags(flags: list[str]) -> None:
    """Reject flags that are not valid IMAP atoms before they reach the STORE command.

    Without this, input such as 'a) (b' or a value containing CR/LF was spliced into
    the command line as is.
    """
    invalid = [flag for flag in flags if not _VALID_IMAP_FLAG.fullmatch(flag)]
    if invalid:
        raise ValueError(f"Invalid IMAP flag(s): {', '.join(repr(f) for f in invalid)}")


def _normalize_flags(flags: list[str]) -> list[str]:
    """Normalize flags: system flags get their canonical backslash form, keywords are left as is.

    "seen", "Seen" and "\\Seen" all become "\\Seen". Anything else is a keyword such as
    "$Label1" or "ProcessedByBot" and must not get a backslash, which would turn it into
    an invalid system flag. An explicit backslash on an unknown name is kept as given.
    """
    normalized = []
    for flag in flags:
        # Handle both bytes and strings from IMAP responses
        if isinstance(flag, bytes):
            flag = flag.decode("utf-8", errors="replace")
        clean_flag = flag.strip()
        system_flag = _SYSTEM_FLAGS.get(clean_flag.lstrip("\\").lower())
        normalized.append(system_flag or clean_flag)
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
        oauth2_kwargs = {
            "auth_type": email_settings.auth_type,
            "email_address": email_settings.email_address,
            "oauth2_provider": email_settings.oauth2_provider,
            "oauth2_client_id": email_settings.oauth2_client_id,
            "oauth2_tenant_id": email_settings.oauth2_tenant_id,
            "oauth2_client_secret": email_settings.oauth2_client_secret,
            "search_chunk_size": email_settings.search_chunk_size,
        }
        self.incoming_client = EmailClient(email_settings.incoming, **oauth2_kwargs)
        self.outgoing_client = EmailClient(
            email_settings.outgoing,
            sender=email_settings.email_address,
            sender_name=email_settings.full_name,
            **oauth2_kwargs,
        )
        self.save_to_sent = email_settings.save_to_sent
        self.sent_folder_name = email_settings.sent_folder_name

    @asynccontextmanager
    async def imap_connection(self, select_mailbox: str = "INBOX"):
        """Reusable IMAP connection context manager for the incoming server."""
        client = self.incoming_client
        imap = client._imap_connect()
        try:
            await imap._client_task
            await imap.wait_hello_from_server()

            await _imap_authenticate(
                imap,
                client.email_server,
                client.auth_type,
                client.email_address,
                client.oauth2_provider,
                client.oauth2_client_id,
                client.oauth2_tenant_id,
                client.oauth2_client_secret,
            )
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
        body: str | None = None,
        text: str | None = None,
    ) -> EmailMetadataPageResponse:
        email_data, total = await self.incoming_client.get_emails_metadata_page(
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
            body=body,
            text=text,
        )
        emails = [EmailMetadata.from_email(data) for data in email_data]
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
                            in_reply_to=email_data.get("in_reply_to"),
                            references=email_data.get("references"),
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
                logger.error(f"Failed to retrieve email {email_id}: {type(e).__name__}: {e!r}")
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
                    auth_type=self.email_settings.auth_type,
                    email_address=self.email_settings.email_address,
                    oauth2_provider=self.email_settings.oauth2_provider,
                    oauth2_client_id=self.email_settings.oauth2_client_id,
                    oauth2_tenant_id=self.email_settings.oauth2_tenant_id,
                    oauth2_client_secret=self.email_settings.oauth2_client_secret,
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

            for flags, delimiter, name in _iter_list_entries(folders):
                folder_info.append({
                    "name": _decode_mailbox_name(name),
                    "delimiter": delimiter,
                    "flags": flags,
                    "can_select": not any(flag.lower() in ("\\noselect", "\\nonexistent") for flag in flags),
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

    async def apply_filter_rule(
        self,
        senders: list[str] | None = None,
        subjects: list[str] | None = None,
        target_folder: str = "",
        source_mailbox: str = "INBOX",
        since: datetime | None = None,
        dry_run: bool = False,
        limit: int | None = None,
        mark_read: bool = False,
    ) -> dict[str, list[str]]:
        """Apply a filter rule using the incoming client."""
        return await self.incoming_client.apply_filter_rule(
            senders=senders,
            subjects=subjects,
            target_folder=target_folder,
            source_mailbox=source_mailbox,
            since=since,
            dry_run=dry_run,
            limit=limit,
            mark_read=mark_read,
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
        self, email_ids: list[str], flags: list[str], operation: str, silent: bool = False, mailbox: str = "INBOX"
    ) -> dict[str, bool]:
        """Core flag modification method using IMAP STORE command."""
        if not email_ids or not flags:
            return {}

        normalized_flags = _normalize_flags(flags)
        _validate_flags(normalized_flags)
        flags_str = f"({' '.join(normalized_flags)})"
        store_cmd = _build_store_command(operation, silent)
        uid_list = ",".join(str(eid) for eid in email_ids)

        try:
            async with self.imap_connection(mailbox) as imap:
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
            logger.error(f"Error in flag modification: {type(e).__name__}: {e!r}")
            return {str(eid): False for eid in email_ids}

    async def add_flags(
        self, email_ids: list[str], flags: list[str], silent: bool = False, mailbox: str = "INBOX"
    ) -> dict[str, bool]:
        """Add flags to emails using +FLAGS operation."""
        return await self._modify_flags(email_ids, flags, "add", silent, mailbox)

    async def remove_flags(
        self, email_ids: list[str], flags: list[str], silent: bool = False, mailbox: str = "INBOX"
    ) -> dict[str, bool]:
        """Remove flags from emails using -FLAGS operation."""
        return await self._modify_flags(email_ids, flags, "remove", silent, mailbox)

    async def replace_flags(
        self, email_ids: list[str], flags: list[str], silent: bool = False, mailbox: str = "INBOX"
    ) -> dict[str, bool]:
        """Replace all flags on emails using FLAGS operation."""
        return await self._modify_flags(email_ids, flags, "replace", silent, mailbox)

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
        imap = client._imap_connect()
        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await _imap_authenticate(
                imap,
                client.email_server,
                client.auth_type,
                client.email_address,
                client.oauth2_provider,
                client.oauth2_client_id,
                client.oauth2_tenant_id,
                client.oauth2_client_secret,
            )
            await _send_imap_id(imap)
            await _select_or_raise(imap, mailbox)

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
                for part in _walk_message_parts(email_message):
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))
                    if "attachment" in content_disposition or _is_attached_message(part):
                        continue
                    if content_type == "text/plain" and not text_body:
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset("utf-8")
                            text_body = _decode_text_part(payload, charset)
                    elif content_type == "text/html" and not html_body:
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset("utf-8")
                            html_body = _decode_text_part(payload, charset)
            # A single-part message that is one attached file has no body text to save.
            elif not _is_attachment_part(email_message):
                content_type = email_message.get_content_type()
                payload = email_message.get_payload(decode=True)
                if payload:
                    charset = email_message.get_content_charset("utf-8")
                    decoded = _decode_text_part(payload, charset)
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
