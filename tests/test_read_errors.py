"""get_emails_content reports why each email could not be read."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_email_server.config import EmailServer, EmailSettings
from mcp_email_server.emails.classic import ClassicEmailHandler, EmailClient, MailboxSelectError

FOUND = {
    "email_id": "1",
    "message_id": "<a@x.com>",
    "subject": "s",
    "from": "a@example.com",
    "to": [],
    "date": datetime.now(timezone.utc),
    "body": "body",
    "attachments": [],
}


@pytest.fixture
def handler():
    settings = EmailSettings.init(
        account_name="t",
        full_name="T",
        email_address="t@example.com",
        user_name="t",
        password="p",
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
    )
    return ClassicEmailHandler(settings)


class TestGetEmailsContentErrors:
    @pytest.mark.asyncio
    async def test_reasons_for_not_found_and_other_errors(self, handler):
        side_effect = [FOUND, None, ValueError("Could not parse email: UnicodeError: bad")]
        with patch.object(handler.incoming_client, "get_email_body_by_id", AsyncMock(side_effect=side_effect)):
            result = await handler.get_emails_content(["1", "2", "3"], mailbox="Archive")

        assert result.retrieved_count == 1
        assert result.failed_ids == ["2", "3"]
        assert result.errors["2"].startswith("Not found in 'Archive'")
        assert result.errors["3"] == "ValueError: Could not parse email: UnicodeError: bad"

    @pytest.mark.asyncio
    async def test_mailbox_error_fails_remaining_ids_without_retrying(self, handler):
        select_error = MailboxSelectError("Mailbox 'Archve' could not be selected: NO")
        mock_get = AsyncMock(side_effect=select_error)
        with patch.object(handler.incoming_client, "get_email_body_by_id", mock_get):
            result = await handler.get_emails_content(["1", "2", "3"], mailbox="Archve")

        mock_get.assert_awaited_once()
        assert result.failed_ids == ["1", "2", "3"]
        assert set(result.errors.values()) == {"Mailbox 'Archve' could not be selected: NO"}

    @pytest.mark.asyncio
    async def test_no_errors_when_all_found(self, handler):
        with patch.object(handler.incoming_client, "get_email_body_by_id", AsyncMock(return_value=FOUND)):
            result = await handler.get_emails_content(["1"])

        assert result.failed_ids == []
        assert result.errors == {}


class TestParseFailureRaises:
    @pytest.mark.asyncio
    async def test_parse_error_is_raised_not_reported_as_missing(self):
        client = EmailClient(EmailServer(user_name="u", password="p", host="imap.example.com", port=993))
        imap = AsyncMock()
        imap._client_task = asyncio.get_running_loop().create_future()
        imap._client_task.set_result(None)
        client.imap_class = MagicMock(return_value=imap)

        with (
            patch("mcp_email_server.emails.classic._imap_authenticate", AsyncMock()),
            patch("mcp_email_server.emails.classic._send_imap_id", AsyncMock()),
            patch("mcp_email_server.emails.classic._select_or_raise", AsyncMock()),
            patch.object(client, "_fetch_email_with_formats", AsyncMock(return_value=[b"data"])),
            patch.object(client, "_extract_raw_email", MagicMock(return_value=b"raw")),
            patch.object(client, "_parse_email_data", MagicMock(side_effect=UnicodeError("bad"))),
            pytest.raises(ValueError, match="Could not parse email: UnicodeError: bad"),
        ):
            await client.get_email_body_by_id("1")
