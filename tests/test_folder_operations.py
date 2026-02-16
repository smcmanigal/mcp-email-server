from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_email_server.config import EmailServer, EmailSettings
from mcp_email_server.emails.classic import ClassicEmailHandler


@pytest.fixture
def email_settings():
    return EmailSettings(
        account_name="test_account",
        full_name="Test User",
        email_address="test@example.com",
        incoming=EmailServer(
            user_name="test_user",
            password="test_password",
            host="imap.example.com",
            port=993,
            use_ssl=True,
        ),
        outgoing=EmailServer(
            user_name="test_user",
            password="test_password",
            host="smtp.example.com",
            port=465,
            use_ssl=True,
        ),
    )


@pytest.fixture
def classic_handler(email_settings):
    return ClassicEmailHandler(email_settings)


class TestListFolders:
    @pytest.mark.asyncio
    async def test_list_folders_returns_expected_structure(self, classic_handler):
        """Test that list_folders parses IMAP LIST response into structured dicts."""
        mock_imap = AsyncMock()
        mock_imap.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren \\Sent) "/" "Sent"',
                b'(\\Noselect \\HasChildren) "/" "Labels"',
            ],
        )

        # Mock imap_connection context manager
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_imap
        mock_ctx.__aexit__.return_value = None
        classic_handler.imap_connection = MagicMock(return_value=mock_ctx)

        result = await classic_handler.list_folders()

        assert len(result) == 3

        # First folder: INBOX
        assert result[0]["name"] == "INBOX"
        assert result[0]["delimiter"] == "/"
        assert "\\HasNoChildren" in result[0]["flags"]
        assert result[0]["can_select"] is True

        # Second folder: Sent
        assert result[1]["name"] == "Sent"
        assert result[1]["can_select"] is True

        # Third folder: Labels (not selectable)
        assert result[2]["name"] == "Labels"
        assert result[2]["can_select"] is False
        assert "\\Noselect" in result[2]["flags"]

        # Verify imap_connection was called with select_mailbox=None
        classic_handler.imap_connection.assert_called_once_with(select_mailbox=None)

    @pytest.mark.asyncio
    async def test_list_folders_with_pattern(self, classic_handler):
        """Test that list_folders passes the pattern argument to IMAP LIST."""
        mock_imap = AsyncMock()
        mock_imap.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
            ],
        )

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_imap
        mock_ctx.__aexit__.return_value = None
        classic_handler.imap_connection = MagicMock(return_value=mock_ctx)

        result = await classic_handler.list_folders(pattern="INBOX*")

        assert len(result) == 1
        assert result[0]["name"] == "INBOX"

        # Verify pattern was passed to imap.list
        mock_imap.list.assert_called_once_with('""', "INBOX*")

    @pytest.mark.asyncio
    async def test_list_folders_empty_response(self, classic_handler):
        """Test that list_folders handles an empty folder list gracefully."""
        mock_imap = AsyncMock()
        mock_imap.list.return_value = ("OK", [])

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_imap
        mock_ctx.__aexit__.return_value = None
        classic_handler.imap_connection = MagicMock(return_value=mock_ctx)

        result = await classic_handler.list_folders()

        assert result == []
