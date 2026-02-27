from unittest.mock import MagicMock, patch

import pytest

from mcp_email_server.config import EmailServer, EmailSettings, ProviderSettings
from mcp_email_server.emails.classic import ClassicEmailHandler
from mcp_email_server.emails.dispatcher import dispatch_handler


class TestDispatcher:
    def test_dispatch_handler_with_email_settings(self):
        """Test dispatch_handler with valid email account."""
        # Create test email settings
        email_settings = EmailSettings(
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

        # Mock the get_settings function to return our settings
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = email_settings

        with patch("mcp_email_server.emails.dispatcher.get_settings", return_value=mock_settings):
            # Call the function
            handler = dispatch_handler("test_account")

            # Verify the result
            assert isinstance(handler, ClassicEmailHandler)
            assert handler.email_settings == email_settings

            # Verify get_account was called correctly
            mock_settings.get_account.assert_called_once_with("test_account")

    def test_dispatch_handler_with_provider_settings(self):
        """Test dispatch_handler with provider account (should raise NotImplementedError)."""
        # Create test provider settings
        provider_settings = ProviderSettings(
            account_name="test_provider",
            provider_name="test",
            api_key="test_key",
        )

        # Mock the get_settings function to return our settings
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = provider_settings

        with patch("mcp_email_server.emails.dispatcher.get_settings", return_value=mock_settings):
            # Call the function and expect NotImplementedError
            with pytest.raises(NotImplementedError):
                dispatch_handler("test_provider")

            # Verify get_account was called correctly
            mock_settings.get_account.assert_called_once_with("test_provider")

    def test_dispatch_handler_with_nonexistent_account(self):
        """Test dispatch_handler with non-existent account (should raise ValueError)."""
        # Mock the get_settings function to return None for get_account
        mock_settings = MagicMock()
        mock_settings.get_account.return_value = None
        mock_settings.get_accounts.return_value = ["account1", "account2"]

        with patch("mcp_email_server.emails.dispatcher.get_settings", return_value=mock_settings):
            # Call the function and expect ValueError
            with pytest.raises(ValueError) as excinfo:
                dispatch_handler("nonexistent_account")

            # Verify the error message
            assert "Account nonexistent_account not found" in str(excinfo.value)

            # Verify get_account was called correctly
            mock_settings.get_account.assert_called_once_with("nonexistent_account")
            mock_settings.get_accounts.assert_called_once()

    def test_dispatch_handler_with_oauth2_email_settings(self):
        """Test dispatch_handler with OAuth2-configured email account dispatches to ClassicEmailHandler."""
        email_settings = EmailSettings(
            account_name="oauth2_account",
            full_name="OAuth2 User",
            email_address="user@outlook.com",
            incoming=EmailServer(
                user_name="user@outlook.com",
                host="outlook.office365.com",
                port=993,
                use_ssl=True,
            ),
            outgoing=EmailServer(
                user_name="user@outlook.com",
                host="smtp.office365.com",
                port=587,
                use_ssl=False,
                start_ssl=True,
            ),
            auth_type="oauth2",
            oauth2_provider="microsoft",
            oauth2_client_id="test-client-id",
            oauth2_tenant_id="test-tenant",
        )

        mock_settings = MagicMock()
        mock_settings.get_account.return_value = email_settings

        with patch("mcp_email_server.emails.dispatcher.get_settings", return_value=mock_settings):
            handler = dispatch_handler("oauth2_account")

            assert isinstance(handler, ClassicEmailHandler)
            assert handler.email_settings == email_settings
            # Verify OAuth2 config was threaded to the clients
            assert handler.incoming_client.auth_type == "oauth2"
            assert handler.incoming_client.oauth2_provider == "microsoft"
            assert handler.incoming_client.oauth2_client_id == "test-client-id"
            assert handler.outgoing_client.auth_type == "oauth2"
            assert handler.outgoing_client.oauth2_provider == "microsoft"
