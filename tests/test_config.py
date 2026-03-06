import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from mcp_email_server.config import (
    CONFIG_PATH,
    EmailServer,
    EmailSettings,
    ProviderSettings,
    Settings,
    get_settings,
    store_settings,
)


def test_config():
    settings = get_settings()
    assert settings.emails == []
    settings.emails.append(
        EmailSettings(
            account_name="email_test",
            full_name="Test User",
            email_address="1oBbE@example.com",
            incoming=EmailServer(
                user_name="test",
                password="test",
                host="imap.gmail.com",
                port=993,
                ssl=True,
            ),
            outgoing=EmailServer(
                user_name="test",
                password="test",
                host="smtp.gmail.com",
                port=587,
                ssl=True,
            ),
        )
    )
    settings.providers.append(ProviderSettings(account_name="provider_test", provider_name="test", api_key="test"))
    store_settings(settings)
    reloaded_settings = get_settings(reload=True)
    assert reloaded_settings == settings

    with pytest.raises(ValidationError):
        settings.add_email(
            EmailSettings(
                account_name="email_test",
                full_name="Test User",
                email_address="1oBbE@example.com",
                incoming=EmailServer(
                    user_name="test",
                    password="test",
                    host="imap.gmail.com",
                    port=993,
                    ssl=True,
                ),
                outgoing=EmailServer(
                    user_name="test",
                    password="test",
                    host="smtp.gmail.com",
                    port=587,
                    ssl=True,
                ),
            )
        )


class TestDbLocation:
    def test_relative_db_location_resolved_to_config_dir(self):
        """Relative db_location should resolve against the config directory, not CWD."""
        # Write a TOML config with a relative db_location (Settings only reads from TOML)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text('db_location = "my.db"\n')
        settings = get_settings(reload=True)
        expected = (CONFIG_PATH.parent / "my.db").resolve().as_posix()
        assert settings.db_location == expected

    def test_absolute_db_location_unchanged(self):
        """Absolute db_location should remain unchanged."""
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text('db_location = "/var/data/test.db"\n')
        settings = get_settings(reload=True)
        assert settings.db_location == "/var/data/test.db"

    def test_default_db_location_is_absolute(self):
        """Default db_location should already be absolute."""
        settings = Settings()
        assert settings.db_location.startswith("/")


class TestOAuth2Config:
    def test_oauth2_settings_defaults_to_password(self):
        """Test that auth_type defaults to 'password' for backward compatibility."""
        settings = EmailSettings(
            account_name="test",
            full_name="Test",
            email_address="test@example.com",
            incoming=EmailServer(user_name="test", password="pass", host="imap.example.com", port=993),
            outgoing=EmailServer(user_name="test", password="pass", host="smtp.example.com", port=465),
        )
        assert settings.auth_type == "password"
        assert settings.oauth2_provider is None
        assert settings.oauth2_client_id is None

    def test_oauth2_settings_roundtrip_toml(self):
        """Test OAuth2 EmailSettings round-trips through TOML (store + reload)."""
        settings = get_settings(reload=True)
        oauth2_account = EmailSettings(
            account_name="oauth2_test",
            full_name="OAuth2 User",
            email_address="user@outlook.com",
            incoming=EmailServer(user_name="user@outlook.com", host="outlook.office365.com", port=993),
            outgoing=EmailServer(user_name="user@outlook.com", host="smtp.office365.com", port=587, use_ssl=False, start_ssl=True),
            auth_type="oauth2",
            oauth2_provider="microsoft",
            oauth2_client_id="test-client-id",
            oauth2_tenant_id="test-tenant-id",
        )
        settings.add_email(oauth2_account)
        store_settings(settings)

        reloaded = get_settings(reload=True)
        reloaded_account = reloaded.get_account("oauth2_test")
        assert reloaded_account is not None
        assert reloaded_account.auth_type == "oauth2"
        assert reloaded_account.oauth2_provider == "microsoft"
        assert reloaded_account.oauth2_client_id == "test-client-id"
        assert reloaded_account.oauth2_tenant_id == "test-tenant-id"
        assert reloaded_account.oauth2_client_secret is None

    def test_oauth2_password_optional(self):
        """Test that password can be empty for OAuth2 accounts."""
        settings = EmailSettings(
            account_name="oauth2_nopw",
            full_name="No Password",
            email_address="user@gmail.com",
            incoming=EmailServer(user_name="user@gmail.com", host="imap.gmail.com", port=993),
            outgoing=EmailServer(user_name="user@gmail.com", host="smtp.gmail.com", port=587),
            auth_type="oauth2",
            oauth2_provider="google",
            oauth2_client_id="google-client-id",
            oauth2_client_secret="google-secret",
        )
        assert settings.incoming.password == ""
        assert settings.outgoing.password == ""
        assert settings.auth_type == "oauth2"

    def test_oauth2_init_factory(self):
        """Test EmailSettings.init() with OAuth2 params."""
        settings = EmailSettings.init(
            account_name="oauth2_init",
            full_name="Init User",
            email_address="user@outlook.com",
            user_name="user@outlook.com",
            imap_host="outlook.office365.com",
            smtp_host="smtp.office365.com",
            auth_type="oauth2",
            oauth2_provider="microsoft",
            oauth2_client_id="my-client-id",
            oauth2_tenant_id="my-tenant",
        )
        assert settings.auth_type == "oauth2"
        assert settings.oauth2_provider == "microsoft"
        assert settings.oauth2_client_id == "my-client-id"
        assert settings.oauth2_tenant_id == "my-tenant"
        assert settings.incoming.password == ""

    def test_from_env_oauth2(self):
        """Test EmailSettings.from_env() with OAuth2 env vars."""
        env = {
            "MCP_EMAIL_SERVER_EMAIL_ADDRESS": "user@outlook.com",
            "MCP_EMAIL_SERVER_IMAP_HOST": "outlook.office365.com",
            "MCP_EMAIL_SERVER_SMTP_HOST": "smtp.office365.com",
            "MCP_EMAIL_SERVER_AUTH_TYPE": "oauth2",
            "MCP_EMAIL_SERVER_OAUTH2_PROVIDER": "microsoft",
            "MCP_EMAIL_SERVER_OAUTH2_CLIENT_ID": "env-client-id",
            "MCP_EMAIL_SERVER_OAUTH2_TENANT_ID": "env-tenant",
        }
        with patch.dict(os.environ, env, clear=False):
            result = EmailSettings.from_env()

        assert result is not None
        assert result.auth_type == "oauth2"
        assert result.oauth2_provider == "microsoft"
        assert result.oauth2_client_id == "env-client-id"
        assert result.oauth2_tenant_id == "env-tenant"

    def test_from_env_oauth2_no_password_required(self):
        """Test from_env doesn't require password when auth_type is oauth2."""
        env = {
            "MCP_EMAIL_SERVER_EMAIL_ADDRESS": "user@gmail.com",
            "MCP_EMAIL_SERVER_IMAP_HOST": "imap.gmail.com",
            "MCP_EMAIL_SERVER_SMTP_HOST": "smtp.gmail.com",
            "MCP_EMAIL_SERVER_AUTH_TYPE": "oauth2",
            "MCP_EMAIL_SERVER_OAUTH2_PROVIDER": "google",
            "MCP_EMAIL_SERVER_OAUTH2_CLIENT_ID": "google-cid",
            "MCP_EMAIL_SERVER_OAUTH2_CLIENT_SECRET": "google-secret",
        }
        # Ensure PASSWORD is not set
        cleaned_env = {k: v for k, v in os.environ.items() if k != "MCP_EMAIL_SERVER_PASSWORD"}
        cleaned_env.update(env)
        with patch.dict(os.environ, cleaned_env, clear=True):
            result = EmailSettings.from_env()

        assert result is not None
        assert result.auth_type == "oauth2"

    def test_from_env_password_still_required_for_password_auth(self):
        """Test from_env still requires password when auth_type is password."""
        env = {
            "MCP_EMAIL_SERVER_EMAIL_ADDRESS": "user@example.com",
            "MCP_EMAIL_SERVER_IMAP_HOST": "imap.example.com",
            "MCP_EMAIL_SERVER_SMTP_HOST": "smtp.example.com",
        }
        # Ensure PASSWORD is not set
        cleaned_env = {k: v for k, v in os.environ.items() if k != "MCP_EMAIL_SERVER_PASSWORD"}
        cleaned_env.update(env)
        with patch.dict(os.environ, cleaned_env, clear=True):
            result = EmailSettings.from_env()

        assert result is None
