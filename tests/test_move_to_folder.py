import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_email_server.config import EmailServer, EmailSettings
from mcp_email_server.emails.classic import ClassicEmailHandler, EmailClient


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
def email_client(email_settings):
    return EmailClient(email_settings.incoming)


@pytest.fixture
def classic_handler(email_settings):
    return ClassicEmailHandler(email_settings)


def _make_mock_imap():
    """Create a mock IMAP connection with standard setup."""
    mock_imap = AsyncMock()
    # _client_task is an awaitable attribute (a Task/Future), not a method call
    future = asyncio.get_event_loop().create_future()
    future.set_result(None)
    mock_imap._client_task = future
    mock_imap.wait_hello_from_server = AsyncMock()
    mock_imap.login = AsyncMock()
    mock_imap.logout = AsyncMock()
    mock_imap.select = AsyncMock()
    mock_imap.list = AsyncMock()
    mock_imap.create = AsyncMock()
    mock_imap.uid = AsyncMock()
    mock_imap.expunge = AsyncMock()
    return mock_imap


class TestMoveEmailsWithMoveCommand:
    """Test moving emails using the MOVE command (RFC 6851)."""

    @pytest.mark.asyncio
    async def test_move_emails_with_move_command(self, email_client):
        """Test successful move using MOVE command."""
        mock_imap = _make_mock_imap()

        # Folder exists
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "Archive"'])

        # MOVE succeeds
        move_response = MagicMock()
        move_response.result = "OK"
        mock_imap.uid.return_value = move_response

        # Patch imap_class to return our mock
        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.move_emails_to_folder(
            email_ids=["100", "101"],
            target_folder="Archive",
            source_mailbox="INBOX",
        )

        assert result["moved"] == ["100", "101"]
        assert result["failed"] == []


class TestMoveEmailsCopyDeleteFallback:
    """Test COPY+DELETE fallback when MOVE is not supported."""

    @pytest.mark.asyncio
    async def test_move_emails_copy_delete_fallback(self, email_client):
        """Test fallback to COPY+DELETE when MOVE fails."""
        mock_imap = _make_mock_imap()

        # Folder exists
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "Archive"'])

        # MOVE raises exception, COPY succeeds, STORE succeeds
        copy_response = MagicMock()
        copy_response.result = "OK"
        store_response = MagicMock()
        store_response.result = "OK"

        call_count = 0

        async def uid_side_effect(cmd, *args):
            nonlocal call_count
            call_count += 1
            if cmd == "move":
                raise OSError("MOVE not supported")
            if cmd == "copy":
                return copy_response
            if cmd == "store":
                return store_response
            return MagicMock(result="OK")

        mock_imap.uid.side_effect = uid_side_effect

        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.move_emails_to_folder(
            email_ids=["100"],
            target_folder="Archive",
            source_mailbox="INBOX",
        )

        assert result["moved"] == ["100"]
        assert result["failed"] == []
        # Verify expunge was called after move
        mock_imap.expunge.assert_called_once()


class TestMoveEmailsCreateMissingFolder:
    """Test folder creation when target folder doesn't exist."""

    @pytest.mark.asyncio
    async def test_move_emails_create_missing_folder(self, email_client):
        """Test that missing folder is created before moving."""
        mock_imap = _make_mock_imap()

        # Folder does NOT exist (list returns empty)
        mock_imap.list.return_value = ("OK", [])
        mock_imap.create.return_value = ("OK", [])

        # MOVE succeeds
        move_response = MagicMock()
        move_response.result = "OK"
        mock_imap.uid.return_value = move_response

        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.move_emails_to_folder(
            email_ids=["100"],
            target_folder="NewFolder",
            source_mailbox="INBOX",
            create_if_missing=True,
        )

        assert result["moved"] == ["100"]
        assert result["failed"] == []
        # Verify create was called
        mock_imap.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_move_emails_skip_folder_creation(self, email_client):
        """Test that folder creation is skipped when create_if_missing=False."""
        mock_imap = _make_mock_imap()

        # MOVE succeeds
        move_response = MagicMock()
        move_response.result = "OK"
        mock_imap.uid.return_value = move_response

        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.move_emails_to_folder(
            email_ids=["100"],
            target_folder="ExistingFolder",
            source_mailbox="INBOX",
            create_if_missing=False,
        )

        assert result["moved"] == ["100"]
        assert result["failed"] == []
        # Verify list/create were NOT called
        mock_imap.list.assert_not_called()
        mock_imap.create.assert_not_called()


class TestMoveEmailsInvalidId:
    """Test handling of invalid email IDs."""

    @pytest.mark.asyncio
    async def test_move_emails_invalid_id(self, email_client):
        """Test that invalid/failing email IDs are reported in failed list."""
        mock_imap = _make_mock_imap()

        # Folder exists
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "Archive"'])

        # MOVE raises for all, COPY also fails
        async def uid_side_effect(cmd, *args):
            if cmd == "move":
                raise OSError("MOVE not supported")
            if cmd == "copy":
                response = MagicMock()
                response.result = "NO"
                return response
            return MagicMock(result="OK")

        mock_imap.uid.side_effect = uid_side_effect

        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.move_emails_to_folder(
            email_ids=["999"],
            target_folder="Archive",
            source_mailbox="INBOX",
        )

        assert result["moved"] == []
        assert result["failed"] == ["999"]
        # Expunge should NOT be called when nothing was moved
        mock_imap.expunge.assert_not_called()

    @pytest.mark.asyncio
    async def test_move_emails_partial_failure(self, email_client):
        """Test mixed success/failure for multiple email IDs."""
        mock_imap = _make_mock_imap()

        # Folder exists
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "Archive"'])

        call_index = 0

        async def uid_side_effect(cmd, email_id=None, *args):
            nonlocal call_index
            if cmd == "move":
                call_index += 1
                if email_id == "100":
                    response = MagicMock()
                    response.result = "OK"
                    return response
                else:
                    raise OSError("MOVE failed")
            if cmd == "copy":
                response = MagicMock()
                response.result = "NO"
                return response
            return MagicMock(result="OK")

        mock_imap.uid.side_effect = uid_side_effect

        email_client.imap_class = MagicMock(return_value=mock_imap)

        result = await email_client.move_emails_to_folder(
            email_ids=["100", "200"],
            target_folder="Archive",
        )

        assert "100" in result["moved"]
        assert "200" in result["failed"]


class TestClassicHandlerMoveEmails:
    """Test ClassicEmailHandler.move_emails_to_folder delegation."""

    @pytest.mark.asyncio
    async def test_handler_delegates_to_client(self, classic_handler):
        """Test that ClassicEmailHandler delegates to incoming_client."""
        expected = {"moved": ["100"], "failed": []}
        classic_handler.incoming_client.move_emails_to_folder = AsyncMock(return_value=expected)

        result = await classic_handler.move_emails_to_folder(
            email_ids=["100"],
            target_folder="Archive",
            source_mailbox="INBOX",
            create_if_missing=True,
        )

        assert result == expected
        classic_handler.incoming_client.move_emails_to_folder.assert_called_once_with(
            ["100"], "Archive", "INBOX", True
        )
