import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.classic import (
    _build_xoauth2_string,
    _imap_authenticate,
    _smtp_authenticate,
)


class TestBuildXoauth2String:
    def test_format(self):
        """Test XOAUTH2 SASL string format."""
        result = _build_xoauth2_string("user@example.com", "token123")
        assert result == "user=user@example.com\x01auth=Bearer token123\x01\x01"

    def test_base64_encoding(self):
        """Test that the string can be base64 encoded for SASL."""
        auth_string = _build_xoauth2_string("user@example.com", "mytoken")
        encoded = base64.b64encode(auth_string.encode()).decode()
        # Verify it decodes back correctly
        decoded = base64.b64decode(encoded).decode()
        assert decoded == auth_string


class TestImapAuthenticate:
    @pytest.mark.asyncio
    async def test_password_auth(self):
        """Test password auth calls imap.login."""
        imap = AsyncMock()
        server = EmailServer(user_name="user", password="pass123", host="imap.example.com", port=993)

        await _imap_authenticate(imap, server, auth_type="password")

        imap.login.assert_awaited_once_with("user", "pass123")
        imap.authenticate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_password_auth_default(self):
        """Test that default auth_type is password."""
        imap = AsyncMock()
        server = EmailServer(user_name="user", password="pass", host="imap.example.com", port=993)

        await _imap_authenticate(imap, server)

        imap.login.assert_awaited_once_with("user", "pass")

    @pytest.mark.asyncio
    async def test_oauth2_auth(self):
        """Test OAuth2 auth calls imap.authenticate with XOAUTH2."""
        imap = AsyncMock()
        mock_response = MagicMock()
        mock_response.result = "OK"
        imap.authenticate.return_value = mock_response

        server = EmailServer(user_name="user@example.com", host="outlook.office365.com", port=993)

        mock_manager = MagicMock()
        mock_manager.get_access_token.return_value = "oauth2_token_abc"

        with patch("mcp_email_server.oauth2.get_token_manager", return_value=mock_manager):
            await _imap_authenticate(
                imap,
                server,
                auth_type="oauth2",
                email_address="user@example.com",
                oauth2_provider="microsoft",
                oauth2_client_id="client123",
                oauth2_tenant_id="tenant456",
            )

        imap.login.assert_not_awaited()
        imap.authenticate.assert_awaited_once()

        # Verify XOAUTH2 mechanism
        call_args = imap.authenticate.call_args
        assert call_args[0][0] == "XOAUTH2"

        # Verify the auth string callback produces correct base64
        auth_callback = call_args[0][1]
        auth_bytes = auth_callback(None)
        decoded = base64.b64decode(auth_bytes).decode()
        assert decoded == "user=user@example.com\x01auth=Bearer oauth2_token_abc\x01\x01"

        # Verify token manager was created with correct params
        mock_manager.get_access_token.assert_called_once_with("user@example.com")

    @pytest.mark.asyncio
    async def test_oauth2_auth_failure(self):
        """Test OAuth2 auth raises RuntimeError on failure."""
        imap = AsyncMock()
        mock_response = MagicMock()
        mock_response.result = "NO"
        imap.authenticate.return_value = mock_response

        server = EmailServer(user_name="user@example.com", host="outlook.office365.com", port=993)

        mock_manager = MagicMock()
        mock_manager.get_access_token.return_value = "bad_token"

        with patch("mcp_email_server.oauth2.get_token_manager", return_value=mock_manager):
            with pytest.raises(RuntimeError, match="IMAP XOAUTH2 authentication failed"):
                await _imap_authenticate(
                    imap,
                    server,
                    auth_type="oauth2",
                    email_address="user@example.com",
                    oauth2_provider="microsoft",
                    oauth2_client_id="client123",
                )


class TestSmtpAuthenticate:
    @pytest.mark.asyncio
    async def test_password_auth(self):
        """Test password auth calls smtp.login."""
        smtp = AsyncMock()
        server = EmailServer(user_name="user", password="pass123", host="smtp.example.com", port=587)

        await _smtp_authenticate(smtp, server, auth_type="password")

        smtp.login.assert_awaited_once_with("user", "pass123")
        smtp.execute_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_password_auth_default(self):
        """Test that default auth_type is password."""
        smtp = AsyncMock()
        server = EmailServer(user_name="user", password="pass", host="smtp.example.com", port=587)

        await _smtp_authenticate(smtp, server)

        smtp.login.assert_awaited_once_with("user", "pass")

    @pytest.mark.asyncio
    async def test_oauth2_auth(self):
        """Test OAuth2 auth sends AUTH XOAUTH2 via execute_command."""
        smtp = AsyncMock()
        server = EmailServer(user_name="user@example.com", host="smtp.office365.com", port=587)

        mock_manager = MagicMock()
        mock_manager.get_access_token.return_value = "smtp_oauth_token"

        with patch("mcp_email_server.oauth2.get_token_manager", return_value=mock_manager):
            await _smtp_authenticate(
                smtp,
                server,
                auth_type="oauth2",
                email_address="user@example.com",
                oauth2_provider="microsoft",
                oauth2_client_id="client123",
                oauth2_tenant_id="tenant456",
            )

        smtp.login.assert_not_awaited()
        smtp.execute_command.assert_awaited_once()

        # Verify the AUTH XOAUTH2 command
        call_args = smtp.execute_command.call_args
        assert call_args[0][0] == b"AUTH"
        assert call_args[0][1] == b"XOAUTH2"

        # Verify the base64 auth string
        auth_b64_bytes = call_args[0][2]
        decoded = base64.b64decode(auth_b64_bytes).decode()
        assert decoded == "user=user@example.com\x01auth=Bearer smtp_oauth_token\x01\x01"

        mock_manager.get_access_token.assert_called_once_with("user@example.com")

    @pytest.mark.asyncio
    async def test_oauth2_google_auth(self):
        """Test OAuth2 auth with Google provider passes client_secret."""
        smtp = AsyncMock()
        server = EmailServer(user_name="user@gmail.com", host="smtp.gmail.com", port=587)

        mock_manager = MagicMock()
        mock_manager.get_access_token.return_value = "google_token"

        with patch("mcp_email_server.oauth2.get_token_manager", return_value=mock_manager) as mock_factory:
            await _smtp_authenticate(
                smtp,
                server,
                auth_type="oauth2",
                email_address="user@gmail.com",
                oauth2_provider="google",
                oauth2_client_id="google_client",
                oauth2_client_secret="google_secret",
            )

        # Verify get_token_manager was called with correct params including client_secret
        mock_factory.assert_called_once_with(
            provider="google",
            client_id="google_client",
            tenant_id="common",
            client_secret="google_secret",
        )
