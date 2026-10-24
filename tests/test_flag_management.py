from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_email_server.config import EmailServer, EmailSettings
from mcp_email_server.emails.classic import (
    ClassicEmailHandler,
    _build_store_command,
    _normalize_flags,
)


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


def _patch_imap_connection(handler, mock_imap):
    """Replace handler.imap_connection with a mock that yields mock_imap."""

    @asynccontextmanager
    async def _mock_conn(select_mailbox="INBOX"):
        yield mock_imap

    original = handler.imap_connection
    handler.imap_connection = _mock_conn
    return original


class TestNormalizeFlags:
    def test_normalize_system_flags(self):
        """System flags get backslash prefix."""
        result = _normalize_flags(["Seen", "Flagged", "Answered"])
        assert result == ["\\Seen", "\\Flagged", "\\Answered"]

    def test_normalize_already_prefixed(self):
        """Flags already with backslash are normalized (not double-prefixed)."""
        result = _normalize_flags(["\\Seen", "\\Flagged"])
        assert result == ["\\Seen", "\\Flagged"]

    def test_normalize_custom_flags(self):
        """Keywords are not system flags and must not get a backslash."""
        result = _normalize_flags(["ProcessedByBot", "MyCustomFlag", "$Label1", "$Junk"])
        assert result == ["ProcessedByBot", "MyCustomFlag", "$Label1", "$Junk"]

    def test_normalize_system_flags_case_insensitive(self):
        result = _normalize_flags(["seen", "\\FLAGGED", "deleted", "Draft", "\\answered"])
        assert result == ["\\Seen", "\\Flagged", "\\Deleted", "\\Draft", "\\Answered"]

    def test_explicit_backslash_on_unknown_name_is_kept(self):
        assert _normalize_flags(["\\Important"]) == ["\\Important"]

    def test_normalize_bytes_flags(self):
        """Bytes flags are decoded and normalized."""
        result = _normalize_flags([b"Seen", b"\\Flagged"])
        assert result == ["\\Seen", "\\Flagged"]

    def test_normalize_empty_list(self):
        """Empty list returns empty list."""
        assert _normalize_flags([]) == []

    def test_normalize_whitespace(self):
        """Flags with whitespace are stripped."""
        result = _normalize_flags(["  Seen  ", " \\Flagged "])
        assert result == ["\\Seen", "\\Flagged"]


class TestBuildStoreCommand:
    def test_add_flags(self):
        assert _build_store_command("add", False) == "+FLAGS"

    def test_add_flags_silent(self):
        assert _build_store_command("add", True) == "+FLAGS.SILENT"

    def test_remove_flags(self):
        assert _build_store_command("remove", False) == "-FLAGS"

    def test_remove_flags_silent(self):
        assert _build_store_command("remove", True) == "-FLAGS.SILENT"

    def test_replace_flags(self):
        assert _build_store_command("replace", False) == "FLAGS"

    def test_replace_flags_silent(self):
        assert _build_store_command("replace", True) == "FLAGS.SILENT"

    def test_invalid_operation(self):
        with pytest.raises(ValueError, match="Invalid operation"):
            _build_store_command("invalid", False)


class TestFlagManagement:
    @pytest.mark.asyncio
    async def test_add_flags_success(self, classic_handler):
        """Test successful batch add_flags operation."""
        mock_imap = AsyncMock()
        mock_response = MagicMock()
        mock_response.result = "OK"
        mock_imap.uid.return_value = mock_response

        original = _patch_imap_connection(classic_handler, mock_imap)
        try:
            result = await classic_handler.add_flags(["100", "101"], ["Seen", "Flagged"])
            assert result == {"100": True, "101": True}
            mock_imap.uid.assert_called_once_with("store", "100,101", "+FLAGS", "(\\Seen \\Flagged)")
        finally:
            classic_handler.imap_connection = original

    @pytest.mark.asyncio
    async def test_remove_flags_success(self, classic_handler):
        """Test successful batch remove_flags operation."""
        mock_imap = AsyncMock()
        mock_response = MagicMock()
        mock_response.result = "OK"
        mock_imap.uid.return_value = mock_response

        original = _patch_imap_connection(classic_handler, mock_imap)
        try:
            result = await classic_handler.remove_flags(["200"], ["Seen"])
            assert result == {"200": True}
            mock_imap.uid.assert_called_once_with("store", "200", "-FLAGS", "(\\Seen)")
        finally:
            classic_handler.imap_connection = original

    @pytest.mark.asyncio
    async def test_replace_flags_success(self, classic_handler):
        """Test successful batch replace_flags operation."""
        mock_imap = AsyncMock()
        mock_response = MagicMock()
        mock_response.result = "OK"
        mock_imap.uid.return_value = mock_response

        original = _patch_imap_connection(classic_handler, mock_imap)
        try:
            result = await classic_handler.replace_flags(["300"], ["Seen", "Answered"])
            assert result == {"300": True}
            mock_imap.uid.assert_called_once_with("store", "300", "FLAGS", "(\\Seen \\Answered)")
        finally:
            classic_handler.imap_connection = original

    @pytest.mark.asyncio
    async def test_add_flags_batch_with_fallback(self, classic_handler):
        """Test that batch failure falls back to individual operations."""
        mock_imap = AsyncMock()
        mock_ok = MagicMock()
        mock_ok.result = "OK"
        # First call (batch) raises exception, subsequent individual calls succeed
        mock_imap.uid.side_effect = [Exception("batch failed"), mock_ok, mock_ok]

        original = _patch_imap_connection(classic_handler, mock_imap)
        try:
            result = await classic_handler.add_flags(["100", "101"], ["Seen"])
            assert result == {"100": True, "101": True}
            # 3 calls: 1 batch (failed) + 2 individual
            assert mock_imap.uid.call_count == 3
        finally:
            classic_handler.imap_connection = original

    @pytest.mark.asyncio
    async def test_modify_flags_empty_ids(self, classic_handler):
        """Test that empty email_ids returns empty dict."""
        result = await classic_handler.add_flags([], ["Seen"])
        assert result == {}

    @pytest.mark.asyncio
    async def test_modify_flags_empty_flags(self, classic_handler):
        """Test that empty flags list returns empty dict."""
        result = await classic_handler.add_flags(["100"], [])
        assert result == {}

    @pytest.mark.asyncio
    async def test_add_flags_silent(self, classic_handler):
        """Test silent mode uses FLAGS.SILENT."""
        mock_imap = AsyncMock()
        mock_response = MagicMock()
        mock_response.result = "OK"
        mock_imap.uid.return_value = mock_response

        original = _patch_imap_connection(classic_handler, mock_imap)
        try:
            result = await classic_handler.add_flags(["100"], ["Seen"], silent=True)
            assert result == {"100": True}
            mock_imap.uid.assert_called_once_with("store", "100", "+FLAGS.SILENT", "(\\Seen)")
        finally:
            classic_handler.imap_connection = original


class TestFlagValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad", ["a) (b", "two words", "inject\r\nA1 LOGOUT", 'quo"te', "back\\slash", "brace{1}", ""]
    )
    async def test_invalid_flag_rejected_before_connecting(self, classic_handler, bad):
        mock_imap = AsyncMock()
        _patch_imap_connection(classic_handler, mock_imap)
        with pytest.raises(ValueError, match="Invalid IMAP flag"):
            await classic_handler.add_flags(["1"], [bad])
        mock_imap.uid.assert_not_called()

    @pytest.mark.asyncio
    async def test_keywords_and_system_flags_sent_as_atoms(self, classic_handler):
        mock_imap = AsyncMock()
        mock_imap.uid = AsyncMock(return_value=MagicMock(result="OK", lines=[]))
        _patch_imap_connection(classic_handler, mock_imap)

        await classic_handler.add_flags(["1"], ["$Label1", "seen", "ProcessedByBot"])

        assert mock_imap.uid.call_args.args == ("store", "1", "+FLAGS", "($Label1 \\Seen ProcessedByBot)")


class TestFlagMailbox:
    """Flag operations act on the requested mailbox, not always INBOX."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("operation", ["add_flags", "remove_flags", "replace_flags"])
    async def test_handler_selects_requested_mailbox(self, classic_handler, operation):
        selected = []
        mock_imap = AsyncMock()
        mock_imap.uid = AsyncMock(return_value=MagicMock(result="OK", lines=[]))

        @asynccontextmanager
        async def _conn(select_mailbox="INBOX"):
            selected.append(select_mailbox)
            yield mock_imap

        classic_handler.imap_connection = _conn
        await getattr(classic_handler, operation)(["1"], ["Flagged"], False, "[Gmail]/Trash")
        assert selected == ["[Gmail]/Trash"]

    @pytest.mark.asyncio
    async def test_handler_defaults_to_inbox(self, classic_handler):
        selected = []

        @asynccontextmanager
        async def _conn(select_mailbox="INBOX"):
            selected.append(select_mailbox)
            yield AsyncMock(uid=AsyncMock(return_value=MagicMock(result="OK", lines=[])))

        classic_handler.imap_connection = _conn
        await classic_handler.add_flags(["1"], ["Seen"])
        assert selected == ["INBOX"]

    def test_cli_passes_mailbox(self):
        from unittest.mock import patch

        from typer.testing import CliRunner

        from mcp_email_server.cli import app

        handler = MagicMock()
        handler.add_flags = AsyncMock(return_value={"5": True})
        with patch("mcp_email_server.cli.flags.dispatch_handler", return_value=handler):
            result = CliRunner().invoke(
                app, ["flags", "add", "-a", "acct", "-f", "Flagged", "--mailbox", "Archive", "5"]
            )
        assert result.exit_code == 0, result.output
        handler.add_flags.assert_awaited_once_with(["5"], ["Flagged"], False, "Archive")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", ["add_email_flags", "remove_email_flags", "replace_email_flags"])
    async def test_mcp_tools_pass_mailbox(self, tool_name):
        from unittest.mock import patch

        from mcp_email_server import app as app_module

        handler = MagicMock()
        method = tool_name.replace("_email", "")
        setattr(handler, method, AsyncMock(return_value={"5": True}))
        with patch("mcp_email_server.app.dispatch_handler", return_value=handler):
            await getattr(app_module, tool_name)("acct", ["5"], ["Flagged"], False, "Archive")
        getattr(handler, method).assert_awaited_once_with(["5"], ["Flagged"], False, "Archive")
