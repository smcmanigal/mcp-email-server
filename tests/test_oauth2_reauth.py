from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from mcp_email_server.app import _pending_oauth2_flows, complete_oauth2_reauth, reauth_oauth2_account
from mcp_email_server.cli import app
from mcp_email_server.config import EmailServer, EmailSettings


def _make_oauth2_account(provider: str = "microsoft", account_name: str = "Test Account") -> EmailSettings:
    return EmailSettings(
        account_name=account_name,
        full_name="Test User",
        email_address="user@example.com",
        incoming=EmailServer(user_name="user@example.com", host="imap.example.com", port=993),
        outgoing=EmailServer(user_name="user@example.com", host="smtp.example.com", port=587),
        auth_type="oauth2",
        oauth2_provider=provider,
        oauth2_client_id="client123",
        oauth2_tenant_id="tenant456",
        oauth2_client_secret="secret789" if provider == "google" else None,
    )


def _make_password_account(account_name: str = "Password Account") -> EmailSettings:
    return EmailSettings(
        account_name=account_name,
        full_name="Test User",
        email_address="user@example.com",
        incoming=EmailServer(user_name="user", password="pass", host="imap.example.com", port=993),
        outgoing=EmailServer(user_name="user", password="pass", host="smtp.example.com", port=587),
    )


runner = CliRunner()


class TestCLIReauth:
    def test_reauth_microsoft(self):
        """Test CLI reauth for Microsoft account runs device code flow."""
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = _make_oauth2_account("microsoft")

        mock_manager = MagicMock()
        mock_manager.uses_device_code_flow = True
        mock_manager.initiate_device_code_flow.return_value = {
            "verification_uri": "https://microsoft.com/devicelogin",
            "user_code": "ABC123",
        }
        mock_manager.complete_device_code_flow.return_value = {"email": "user@example.com"}
        mock_manager.get_access_token.return_value = "token123"

        with (
            patch("mcp_email_server.cli.accounts.get_settings", return_value=mock_settings),
            patch("mcp_email_server.oauth2.get_token_manager", return_value=mock_manager),
        ):
            result = runner.invoke(app, ["accounts", "reauth", "-a", "Test Account"])

        assert result.exit_code == 0
        assert "re-authenticated successfully" in result.output.lower()
        mock_manager.initiate_device_code_flow.assert_called_once()
        mock_manager.complete_device_code_flow.assert_called_once()
        mock_manager.get_access_token.assert_called_once_with("user@example.com")

    def test_reauth_google(self):
        """Test CLI reauth for Google account runs browser flow."""
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = _make_oauth2_account("google")

        mock_manager = MagicMock()
        mock_manager.uses_device_code_flow = False
        mock_manager.run_auth_flow.return_value = {"email": "user@example.com"}
        mock_manager.get_access_token.return_value = "token123"

        with (
            patch("mcp_email_server.cli.accounts.get_settings", return_value=mock_settings),
            patch("mcp_email_server.oauth2.get_token_manager", return_value=mock_manager),
        ):
            result = runner.invoke(app, ["accounts", "reauth", "-a", "Test Account"])

        assert result.exit_code == 0
        assert "re-authenticated successfully" in result.output.lower()
        mock_manager.run_auth_flow.assert_called_once_with(email="user@example.com")
        mock_manager.get_access_token.assert_called_once_with("user@example.com")

    def test_reauth_account_not_found(self):
        """Test CLI reauth fails when account doesn't exist."""
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = None

        with patch("mcp_email_server.cli.accounts.get_settings", return_value=mock_settings):
            result = runner.invoke(app, ["accounts", "reauth", "-a", "Missing"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_reauth_non_oauth2_account(self):
        """Test CLI reauth fails for password-based account."""
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = _make_password_account()

        with patch("mcp_email_server.cli.accounts.get_settings", return_value=mock_settings):
            result = runner.invoke(app, ["accounts", "reauth", "-a", "Password Account"])

        assert result.exit_code == 1
        assert "not an oauth2 account" in result.output.lower()


class TestMCPReauth:
    @pytest.mark.asyncio
    async def test_reauth_microsoft_mcp(self):
        """Test MCP reauth tool for Microsoft returns device code info."""
        account = _make_oauth2_account("microsoft")
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = account

        mock_manager = MagicMock()
        mock_manager.uses_device_code_flow = True
        mock_manager.initiate_device_code_flow.return_value = {
            "verification_uri": "https://microsoft.com/devicelogin",
            "user_code": "XYZ789",
            "message": "Go to https://microsoft.com/devicelogin and enter code XYZ789",
        }

        with (
            patch("mcp_email_server.app.get_settings", return_value=mock_settings),
            patch("mcp_email_server.oauth2.get_token_manager", return_value=mock_manager),
        ):
            result = await reauth_oauth2_account(account_name="Test Account")

        assert result["user_code"] == "XYZ789"
        assert "verification_uri" in result
        # Verify pending flow was stored
        assert "reauth:Test Account" in _pending_oauth2_flows
        # Clean up
        del _pending_oauth2_flows["reauth:Test Account"]

    @pytest.mark.asyncio
    async def test_reauth_google_mcp(self):
        """Test MCP reauth tool for Google completes in one call."""
        account = _make_oauth2_account("google")
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = account

        mock_manager = MagicMock()
        mock_manager.uses_device_code_flow = False
        mock_manager.run_auth_flow.return_value = {"email": "user@example.com"}

        with (
            patch("mcp_email_server.app.get_settings", return_value=mock_settings),
            patch("mcp_email_server.oauth2.get_token_manager", return_value=mock_manager),
            patch("asyncio.to_thread", return_value={"email": "user@example.com"}),
        ):
            result = await reauth_oauth2_account(account_name="Test Account")

        assert result["complete"] is True
        assert "re-authenticated" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_reauth_account_not_found_mcp(self):
        """Test MCP reauth tool fails when account doesn't exist."""
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = None

        with (
            patch("mcp_email_server.app.get_settings", return_value=mock_settings),
            pytest.raises(ValueError, match="not found"),
        ):
            await reauth_oauth2_account(account_name="Missing")

    @pytest.mark.asyncio
    async def test_reauth_non_oauth2_mcp(self):
        """Test MCP reauth tool fails for non-OAuth2 account."""
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = _make_password_account()

        with (
            patch("mcp_email_server.app.get_settings", return_value=mock_settings),
            pytest.raises(ValueError, match="not an OAuth2 account"),
        ):
            await reauth_oauth2_account(account_name="Password Account")

    @pytest.mark.asyncio
    async def test_complete_reauth_microsoft(self):
        """Test completing Microsoft reauth flow."""
        mock_manager = MagicMock()
        mock_manager.complete_device_code_flow.return_value = {"email": "user@example.com"}

        _pending_oauth2_flows["reauth:Test Account"] = {
            "flow": {"user_code": "ABC"},
            "manager": mock_manager,
            "email_address": "user@example.com",
            "created_at": __import__("time").time(),
        }

        with patch("asyncio.to_thread", return_value={"email": "user@example.com"}):
            result = await complete_oauth2_reauth(account_name="Test Account")

        assert "re-authenticated" in result.lower()
        assert "reauth:Test Account" not in _pending_oauth2_flows

    @pytest.mark.asyncio
    async def test_complete_reauth_no_pending(self):
        """Test completing reauth fails when no pending flow exists."""
        with pytest.raises(ValueError, match="No pending re-auth flow"):
            await complete_oauth2_reauth(account_name="Nonexistent")
