from __future__ import annotations

import abc
import json
import os
from pathlib import Path
from typing import ClassVar

from mcp_email_server.log import logger

DEFAULT_CONFIG_DIR = Path(os.getenv("MCP_EMAIL_SERVER_CONFIG_PATH", "~/.config/zerolib/mcp_email_server/config.toml")).expanduser().resolve().parent

PROVIDER_DEFAULTS: dict[str, dict[str, str | int | bool]] = {
    "microsoft": {
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_ssl": False,
        "smtp_start_ssl": True,
    },
    "google": {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_ssl": False,
        "smtp_start_ssl": True,
    },
}


class OAuth2TokenManager(abc.ABC):
    """Base class for OAuth2 token managers."""

    @abc.abstractmethod
    def get_access_token(self, email: str) -> str:
        """Return a valid access token, refreshing if needed.

        Raises RuntimeError if no cached credentials are available.
        """

    @abc.abstractmethod
    def initiate_device_code_flow(self) -> dict:
        """Start the device code authorization flow.

        Returns a dict with at least 'verification_uri' and 'user_code'.
        For providers that don't support device code flow, raises RuntimeError.
        """

    @abc.abstractmethod
    def complete_device_code_flow(self, flow: dict) -> dict:
        """Complete the device code flow after user action.

        Returns account info on success, raises RuntimeError on failure.
        """

    def run_auth_flow(self, email: str) -> dict:
        """Run an interactive browser-based auth flow (authorization code with localhost redirect).

        Default implementation falls back to device code flow.
        Override for providers that need browser-based auth (e.g., Google).

        Returns account info on success, raises RuntimeError on failure.
        """
        flow = self.initiate_device_code_flow()
        return self.complete_device_code_flow(flow)

    def refresh_access_token(self, email: str) -> str:
        """Try to get a new access token using cached refresh credentials only (no user interaction).

        Returns a valid access token if refresh succeeded.
        Raises RuntimeError if no cached credentials exist, refresh token is missing/revoked, etc.

        Default implementation delegates to get_access_token(), which works for providers
        like MSAL that handle refresh internally via acquire_token_silent().
        """
        return self.get_access_token(email)

    @abc.abstractmethod
    def remove_account(self, email: str) -> bool:
        """Clear cached tokens for the given email. Returns True if tokens were removed."""

    @property
    def uses_device_code_flow(self) -> bool:
        """Whether this provider uses device code flow (True) or browser redirect flow (False)."""
        return True


def _ensure_file_permissions(path: Path) -> None:
    """Set file permissions to 0600 (owner read/write only)."""
    try:
        path.chmod(0o600)
    except OSError as e:
        logger.warning(f"Could not set file permissions on {path}: {e}")


class MSALTokenManager(OAuth2TokenManager):
    """Microsoft 365 OAuth2 token manager using MSAL."""

    DEFAULT_SCOPES: ClassVar[list[str]] = [
        "https://outlook.office365.com/IMAP.AccessAsUser.All",
        "https://outlook.office365.com/SMTP.Send",
    ]

    def __init__(
        self,
        client_id: str,
        tenant_id: str = "common",
        scopes: list[str] | None = None,
        cache_path: Path | None = None,
    ):
        import msal

        self.client_id = client_id
        self.tenant_id = tenant_id
        self.scopes = scopes or self.DEFAULT_SCOPES
        self.cache_path = cache_path or DEFAULT_CONFIG_DIR / "oauth2_token_cache.json"

        # Initialize MSAL token cache
        self._cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            self._cache.deserialize(self.cache_path.read_text())

        self._app = msal.PublicClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=self._cache,
        )

    def _save_cache(self) -> None:
        """Persist the token cache to disk."""
        if self._cache.has_state_changed:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(self._cache.serialize())
            _ensure_file_permissions(self.cache_path)

    def get_access_token(self, email: str) -> str:
        accounts = self._app.get_accounts(username=email)
        if not accounts:
            raise RuntimeError(
                f"No cached credentials for {email}. Run OAuth2 setup first."
            )

        result = self._app.acquire_token_silent(self.scopes, account=accounts[0])
        self._save_cache()

        if result and "access_token" in result:
            return result["access_token"]

        error = result.get("error_description", "Unknown error") if result else "No result from token refresh"
        raise RuntimeError(f"Failed to acquire token for {email}: {error}")

    def initiate_device_code_flow(self) -> dict:
        flow = self._app.initiate_device_flow(scopes=self.scopes)
        if "user_code" not in flow:
            error = flow.get("error_description", flow.get("error", "Unknown error"))
            raise RuntimeError(f"Failed to initiate device code flow: {error}")
        return flow

    def complete_device_code_flow(self, flow: dict) -> dict:
        result = self._app.acquire_token_by_device_flow(flow)
        self._save_cache()

        if "access_token" in result:
            return {
                "email": result.get("id_token_claims", {}).get("preferred_username", ""),
                "name": result.get("id_token_claims", {}).get("name", ""),
                "token_type": result.get("token_type", "Bearer"),
            }

        error = result.get("error_description", result.get("error", "Unknown error"))
        raise RuntimeError(f"Failed to complete device code flow: {error}")

    def remove_account(self, email: str) -> bool:
        accounts = self._app.get_accounts(username=email)
        if not accounts:
            return False
        for account in accounts:
            self._app.remove_account(account)
        self._save_cache()
        return True


class GoogleTokenManager(OAuth2TokenManager):
    """Google OAuth2 token manager."""

    DEFAULT_SCOPES: ClassVar[list[str]] = ["https://mail.google.com/"]

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scopes: list[str] | None = None,
        cache_path: Path | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes or self.DEFAULT_SCOPES
        self.cache_path = cache_path or DEFAULT_CONFIG_DIR / "google_token_cache.json"

    def _load_credentials(self, email: str):
        """Load stored credentials for the given email."""
        from google.oauth2.credentials import Credentials

        if not self.cache_path.exists():
            return None

        cache_data = json.loads(self.cache_path.read_text())
        account_data = cache_data.get(email)
        if not account_data:
            return None

        return Credentials(
            token=account_data.get("token"),
            refresh_token=account_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",  # noqa: S106
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=self.scopes,
        )

    def _save_credentials(self, email: str, credentials) -> None:
        """Persist credentials for the given email."""
        cache_data = {}
        if self.cache_path.exists():
            cache_data = json.loads(self.cache_path.read_text())

        cache_data[email] = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        }

        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(cache_data, indent=2))
        _ensure_file_permissions(self.cache_path)

    def get_access_token(self, email: str) -> str:
        import google.auth.transport.requests

        credentials = self._load_credentials(email)
        if credentials is None:
            raise RuntimeError(
                f"No cached credentials for {email}. Run OAuth2 setup first."
            )

        if credentials.expired and credentials.refresh_token:
            credentials.refresh(google.auth.transport.requests.Request())
            self._save_credentials(email, credentials)

        if credentials.token:
            return credentials.token

        raise RuntimeError(f"Failed to acquire token for {email}")

    def initiate_device_code_flow(self) -> dict:
        raise RuntimeError(
            "Google does not support device code flow for the https://mail.google.com/ scope. "
            "Use run_auth_flow() instead, which opens a browser for authorization."
        )

    def complete_device_code_flow(self, flow: dict) -> dict:
        raise RuntimeError(
            "Google does not support device code flow. Use run_auth_flow() instead."
        )

    def run_auth_flow(self, email: str) -> dict:
        """Run authorization code flow with localhost redirect. Opens a browser window."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=self.scopes,
        )

        creds = flow.run_local_server(port=0, open_browser=False)

        self._save_credentials(email, creds)

        return {
            "email": email,
            "token_type": "Bearer",
        }

    def refresh_access_token(self, email: str) -> str:
        """Force-refresh the access token using the stored refresh token (no browser needed)."""
        import google.auth.transport.requests

        credentials = self._load_credentials(email)
        if credentials is None:
            raise RuntimeError(f"No cached credentials for {email}. Run OAuth2 setup first.")

        if not credentials.refresh_token:
            raise RuntimeError(f"No refresh token cached for {email}. Full re-authentication required.")

        credentials.refresh(google.auth.transport.requests.Request())
        self._save_credentials(email, credentials)

        if credentials.token:
            return credentials.token

        raise RuntimeError(f"Failed to refresh token for {email}")

    @property
    def uses_device_code_flow(self) -> bool:
        return False

    def remove_account(self, email: str) -> bool:
        if not self.cache_path.exists():
            return False

        cache_data = json.loads(self.cache_path.read_text())
        if email not in cache_data:
            return False

        del cache_data[email]
        self.cache_path.write_text(json.dumps(cache_data, indent=2))
        _ensure_file_permissions(self.cache_path)
        return True


def get_token_manager(
    provider: str,
    client_id: str,
    tenant_id: str = "common",
    client_secret: str | None = None,
) -> OAuth2TokenManager:
    """Factory function to create the appropriate token manager.

    Args:
        provider: "microsoft" or "google"
        client_id: OAuth2 application client ID
        tenant_id: Azure AD tenant ID (Microsoft only, defaults to "common")
        client_secret: Client secret (required for Google)
    """
    if provider == "microsoft":
        return MSALTokenManager(client_id=client_id, tenant_id=tenant_id)
    elif provider == "google":
        if not client_secret:
            raise ValueError("client_secret is required for Google OAuth2")
        return GoogleTokenManager(client_id=client_id, client_secret=client_secret)
    else:
        raise ValueError(f"Unsupported OAuth2 provider: {provider}. Use 'microsoft' or 'google'.")
