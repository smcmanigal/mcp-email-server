import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_email_server.oauth2 import (
    GoogleTokenManager,
    MSALTokenManager,
    get_token_manager,
)


class TestMSALTokenManager:
    def test_get_access_token_success(self, tmp_path):
        """Test successful token acquisition from cache."""
        cache_path = tmp_path / "token_cache.json"

        with (
            patch("msal.SerializableTokenCache") as mock_cache_cls,
            patch("msal.PublicClientApplication") as mock_app_cls,
        ):
            mock_cache = MagicMock()
            mock_cache.has_state_changed = False
            mock_cache_cls.return_value = mock_cache

            mock_app = MagicMock()
            mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
            mock_app.acquire_token_silent.return_value = {"access_token": "test_token_123"}
            mock_app_cls.return_value = mock_app

            manager = MSALTokenManager(client_id="test_client", tenant_id="test_tenant", cache_path=cache_path)
            token = manager.get_access_token("user@example.com")

            assert token == "test_token_123"
            mock_app.get_accounts.assert_called_once_with(username="user@example.com")
            mock_app.acquire_token_silent.assert_called_once()

    def test_get_access_token_no_cached_account(self, tmp_path):
        """Test RuntimeError when no cached credentials exist."""
        cache_path = tmp_path / "token_cache.json"

        with (
            patch("msal.SerializableTokenCache") as mock_cache_cls,
            patch("msal.PublicClientApplication") as mock_app_cls,
        ):
            mock_cache = MagicMock()
            mock_cache.has_state_changed = False
            mock_cache_cls.return_value = mock_cache

            mock_app = MagicMock()
            mock_app.get_accounts.return_value = []
            mock_app_cls.return_value = mock_app

            manager = MSALTokenManager(client_id="test_client", cache_path=cache_path)

            with pytest.raises(RuntimeError, match="No cached credentials"):
                manager.get_access_token("user@example.com")

    def test_get_access_token_refresh_failure(self, tmp_path):
        """Test RuntimeError when token refresh fails."""
        cache_path = tmp_path / "token_cache.json"

        with (
            patch("msal.SerializableTokenCache") as mock_cache_cls,
            patch("msal.PublicClientApplication") as mock_app_cls,
        ):
            mock_cache = MagicMock()
            mock_cache.has_state_changed = False
            mock_cache_cls.return_value = mock_cache

            mock_app = MagicMock()
            mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
            mock_app.acquire_token_silent.return_value = {"error_description": "Token expired"}
            mock_app_cls.return_value = mock_app

            manager = MSALTokenManager(client_id="test_client", cache_path=cache_path)

            with pytest.raises(RuntimeError, match="Failed to acquire token"):
                manager.get_access_token("user@example.com")

    def test_initiate_device_code_flow_success(self, tmp_path):
        """Test successful device code flow initiation."""
        cache_path = tmp_path / "token_cache.json"

        with (
            patch("msal.SerializableTokenCache") as mock_cache_cls,
            patch("msal.PublicClientApplication") as mock_app_cls,
        ):
            mock_cache = MagicMock()
            mock_cache.has_state_changed = False
            mock_cache_cls.return_value = mock_cache

            mock_app = MagicMock()
            mock_app.initiate_device_flow.return_value = {
                "user_code": "ABCD1234",
                "verification_uri": "https://login.microsoftonline.com/common/oauth2/deviceauth",
            }
            mock_app_cls.return_value = mock_app

            manager = MSALTokenManager(client_id="test_client", cache_path=cache_path)
            flow = manager.initiate_device_code_flow()

            assert flow["user_code"] == "ABCD1234"
            assert "verification_uri" in flow

    def test_initiate_device_code_flow_error(self, tmp_path):
        """Test RuntimeError when device code flow fails."""
        cache_path = tmp_path / "token_cache.json"

        with (
            patch("msal.SerializableTokenCache") as mock_cache_cls,
            patch("msal.PublicClientApplication") as mock_app_cls,
        ):
            mock_cache = MagicMock()
            mock_cache.has_state_changed = False
            mock_cache_cls.return_value = mock_cache

            mock_app = MagicMock()
            mock_app.initiate_device_flow.return_value = {"error": "invalid_client"}
            mock_app_cls.return_value = mock_app

            manager = MSALTokenManager(client_id="bad_client", cache_path=cache_path)

            with pytest.raises(RuntimeError, match="Failed to initiate"):
                manager.initiate_device_code_flow()

    def test_complete_device_code_flow_success(self, tmp_path):
        """Test successful device code flow completion."""
        cache_path = tmp_path / "token_cache.json"

        with (
            patch("msal.SerializableTokenCache") as mock_cache_cls,
            patch("msal.PublicClientApplication") as mock_app_cls,
        ):
            mock_cache = MagicMock()
            mock_cache.has_state_changed = True
            mock_cache.serialize.return_value = "{}"
            mock_cache_cls.return_value = mock_cache

            mock_app = MagicMock()
            mock_app.acquire_token_by_device_flow.return_value = {
                "access_token": "new_token",
                "token_type": "Bearer",
                "id_token_claims": {"preferred_username": "user@example.com", "name": "Test User"},
            }
            mock_app_cls.return_value = mock_app

            manager = MSALTokenManager(client_id="test_client", cache_path=cache_path)
            result = manager.complete_device_code_flow({"device_code": "xxx"})

            assert result["email"] == "user@example.com"
            assert result["name"] == "Test User"

    def test_remove_account_success(self, tmp_path):
        """Test successful account removal."""
        cache_path = tmp_path / "token_cache.json"

        with (
            patch("msal.SerializableTokenCache") as mock_cache_cls,
            patch("msal.PublicClientApplication") as mock_app_cls,
        ):
            mock_cache = MagicMock()
            mock_cache.has_state_changed = True
            mock_cache.serialize.return_value = "{}"
            mock_cache_cls.return_value = mock_cache

            mock_account = {"username": "user@example.com"}
            mock_app = MagicMock()
            mock_app.get_accounts.return_value = [mock_account]
            mock_app_cls.return_value = mock_app

            manager = MSALTokenManager(client_id="test_client", cache_path=cache_path)
            result = manager.remove_account("user@example.com")

            assert result is True
            mock_app.remove_account.assert_called_once_with(mock_account)

    def test_remove_account_not_found(self, tmp_path):
        """Test remove_account returns False when account doesn't exist."""
        cache_path = tmp_path / "token_cache.json"

        with (
            patch("msal.SerializableTokenCache") as mock_cache_cls,
            patch("msal.PublicClientApplication") as mock_app_cls,
        ):
            mock_cache = MagicMock()
            mock_cache.has_state_changed = False
            mock_cache_cls.return_value = mock_cache

            mock_app = MagicMock()
            mock_app.get_accounts.return_value = []
            mock_app_cls.return_value = mock_app

            manager = MSALTokenManager(client_id="test_client", cache_path=cache_path)
            result = manager.remove_account("nobody@example.com")

            assert result is False

    def test_cache_save_on_state_change(self, tmp_path):
        """Test that cache is persisted when state changes."""
        cache_path = tmp_path / "token_cache.json"

        with (
            patch("msal.SerializableTokenCache") as mock_cache_cls,
            patch("msal.PublicClientApplication") as mock_app_cls,
        ):
            mock_cache = MagicMock()
            mock_cache.has_state_changed = True
            mock_cache.serialize.return_value = '{"cached": true}'
            mock_cache_cls.return_value = mock_cache

            mock_app = MagicMock()
            mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
            mock_app.acquire_token_silent.return_value = {"access_token": "tok"}
            mock_app_cls.return_value = mock_app

            manager = MSALTokenManager(client_id="test_client", cache_path=cache_path)
            manager.get_access_token("user@example.com")

            assert cache_path.exists()
            assert json.loads(cache_path.read_text()) == {"cached": True}

    def test_cache_load_existing(self, tmp_path):
        """Test that existing cache is loaded on init."""
        cache_path = tmp_path / "token_cache.json"
        cache_path.write_text('{"existing": "data"}')

        with patch("msal.SerializableTokenCache") as mock_cache_cls, patch("msal.PublicClientApplication"):
            mock_cache = MagicMock()
            mock_cache.has_state_changed = False
            mock_cache_cls.return_value = mock_cache

            MSALTokenManager(client_id="test_client", cache_path=cache_path)

            mock_cache.deserialize.assert_called_once_with('{"existing": "data"}')


class TestGoogleTokenManager:
    def test_get_access_token_success(self, tmp_path):
        """Test successful token acquisition."""
        cache_path = tmp_path / "google_cache.json"
        cache_path.write_text(
            json.dumps({
                "user@gmail.com": {
                    "token": "valid_token",
                    "refresh_token": "refresh_tok",
                    "client_id": "cid",
                    "client_secret": "csec",
                }
            })
        )

        with patch("google.oauth2.credentials.Credentials") as mock_creds_cls:
            mock_creds = MagicMock()
            mock_creds.expired = False
            mock_creds.token = "valid_token"
            mock_creds_cls.return_value = mock_creds

            manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=cache_path)
            token = manager.get_access_token("user@gmail.com")

            assert token == "valid_token"

    def test_get_access_token_no_cached_creds(self, tmp_path):
        """Test RuntimeError when no cached credentials."""
        cache_path = tmp_path / "google_cache.json"

        manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=cache_path)

        with pytest.raises(RuntimeError, match="No cached credentials"):
            manager.get_access_token("user@gmail.com")

    def test_get_access_token_refresh(self, tmp_path):
        """Test token refresh when expired."""
        cache_path = tmp_path / "google_cache.json"
        cache_path.write_text(
            json.dumps({
                "user@gmail.com": {
                    "token": "expired_token",
                    "refresh_token": "refresh_tok",
                    "client_id": "cid",
                    "client_secret": "csec",
                }
            })
        )

        with (
            patch("google.oauth2.credentials.Credentials") as mock_creds_cls,
            patch("google.auth.transport.requests.Request") as mock_request_cls,
        ):
            mock_creds = MagicMock()
            mock_creds.expired = True
            mock_creds.refresh_token = "refresh_tok"
            mock_creds.token = "new_token"
            mock_creds.client_id = "cid"
            mock_creds.client_secret = "csec"
            mock_creds_cls.return_value = mock_creds

            manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=cache_path)
            token = manager.get_access_token("user@gmail.com")

            assert token == "new_token"
            mock_creds.refresh.assert_called_once_with(mock_request_cls.return_value)

    def test_uses_device_code_flow_is_false(self, tmp_path):
        """Test that Google uses browser redirect, not device code flow."""
        manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=tmp_path / "cache.json")
        assert manager.uses_device_code_flow is False

    def test_initiate_device_code_flow_raises(self, tmp_path):
        """Test that device code flow raises RuntimeError for Google."""
        manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=tmp_path / "cache.json")
        with pytest.raises(RuntimeError, match="does not support device code flow"):
            manager.initiate_device_code_flow()

    def test_run_auth_flow_success(self, tmp_path):
        """Test successful browser-based auth flow."""
        cache_path = tmp_path / "google_cache.json"

        with patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_config") as mock_from_config:
            mock_flow = MagicMock()
            mock_creds = MagicMock()
            mock_creds.token = "new_access_token"
            mock_creds.refresh_token = "new_refresh_token"
            mock_creds.client_id = "cid"
            mock_creds.client_secret = "csec"
            mock_flow.run_local_server.return_value = mock_creds
            mock_from_config.return_value = mock_flow

            manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=cache_path)
            result = manager.run_auth_flow(email="user@gmail.com")

            assert result["email"] == "user@gmail.com"
            assert result["token_type"] == "Bearer"
            mock_flow.run_local_server.assert_called_once_with(port=0, open_browser=False)

            # Verify credentials were saved
            assert cache_path.exists()
            saved = json.loads(cache_path.read_text())
            assert "user@gmail.com" in saved

    def test_refresh_access_token_success(self, tmp_path):
        """Test force-refresh using stored refresh token."""
        cache_path = tmp_path / "google_cache.json"
        cache_path.write_text(
            json.dumps({
                "user@gmail.com": {
                    "token": "old_token",
                    "refresh_token": "refresh_tok",
                    "client_id": "cid",
                    "client_secret": "csec",
                }
            })
        )

        with (
            patch("google.oauth2.credentials.Credentials") as mock_creds_cls,
            patch("google.auth.transport.requests.Request") as mock_request_cls,
        ):
            mock_creds = MagicMock()
            mock_creds.refresh_token = "refresh_tok"
            mock_creds.token = "refreshed_token"
            mock_creds.client_id = "cid"
            mock_creds.client_secret = "csec"
            mock_creds_cls.return_value = mock_creds

            manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=cache_path)
            token = manager.refresh_access_token("user@gmail.com")

            assert token == "refreshed_token"
            # Always calls refresh regardless of expiry state
            mock_creds.refresh.assert_called_once_with(mock_request_cls.return_value)

    def test_refresh_access_token_no_cached_creds(self, tmp_path):
        """Test refresh_access_token raises when no cached credentials."""
        cache_path = tmp_path / "google_cache.json"
        manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=cache_path)

        with pytest.raises(RuntimeError, match="No cached credentials"):
            manager.refresh_access_token("user@gmail.com")

    def test_refresh_access_token_no_refresh_token(self, tmp_path):
        """Test refresh_access_token raises when no refresh token is cached."""
        cache_path = tmp_path / "google_cache.json"
        cache_path.write_text(
            json.dumps({
                "user@gmail.com": {
                    "token": "old_token",
                    "refresh_token": None,
                    "client_id": "cid",
                    "client_secret": "csec",
                }
            })
        )

        with patch("google.oauth2.credentials.Credentials") as mock_creds_cls:
            mock_creds = MagicMock()
            mock_creds.refresh_token = None
            mock_creds_cls.return_value = mock_creds

            manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=cache_path)

            with pytest.raises(RuntimeError, match="No refresh token"):
                manager.refresh_access_token("user@gmail.com")

    def test_remove_account_success(self, tmp_path):
        """Test successful account removal."""
        cache_path = tmp_path / "google_cache.json"
        cache_path.write_text(
            json.dumps({
                "user@gmail.com": {"token": "tok", "refresh_token": "ref"},
                "other@gmail.com": {"token": "tok2", "refresh_token": "ref2"},
            })
        )

        manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=cache_path)
        result = manager.remove_account("user@gmail.com")

        assert result is True
        remaining = json.loads(cache_path.read_text())
        assert "user@gmail.com" not in remaining
        assert "other@gmail.com" in remaining

    def test_remove_account_not_found(self, tmp_path):
        """Test remove_account returns False when account doesn't exist."""
        cache_path = tmp_path / "google_cache.json"
        cache_path.write_text(json.dumps({}))

        manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=cache_path)
        result = manager.remove_account("nobody@gmail.com")

        assert result is False

    def test_remove_account_no_cache_file(self, tmp_path):
        """Test remove_account returns False when cache file doesn't exist."""
        cache_path = tmp_path / "google_cache.json"

        manager = GoogleTokenManager(client_id="cid", client_secret="csec", cache_path=cache_path)
        result = manager.remove_account("user@gmail.com")

        assert result is False


class TestGetTokenManager:
    def test_microsoft_provider(self):
        """Test factory returns MSALTokenManager for microsoft."""
        with patch("msal.SerializableTokenCache"), patch("msal.PublicClientApplication"):
            manager = get_token_manager(provider="microsoft", client_id="test_cid")
            assert isinstance(manager, MSALTokenManager)

    def test_google_provider(self):
        """Test factory returns GoogleTokenManager for google."""
        manager = get_token_manager(provider="google", client_id="test_cid", client_secret="test_secret")
        assert isinstance(manager, GoogleTokenManager)

    def test_google_requires_client_secret(self):
        """Test ValueError when Google provider is missing client_secret."""
        with pytest.raises(ValueError, match="client_secret is required"):
            get_token_manager(provider="google", client_id="test_cid")

    def test_unknown_provider(self):
        """Test ValueError for unsupported provider."""
        with pytest.raises(ValueError, match="Unsupported OAuth2 provider"):
            get_token_manager(provider="yahoo", client_id="test_cid")
