# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies and pre-commit hooks
make install          # uv sync + pre-commit install

# Run tests
make test             # pytest with coverage
uv run python -m pytest tests/test_config.py -vv -s   # single test file
uv run python -m pytest -k "test_dispatch_handler" -vv # single test by name

# Lint / code quality
make check            # lock file check + pre-commit + deptry

# Run the MCP server locally (stdio transport)
uv run mcp-email-server stdio

# Configure via UI
uv run mcp-email-server ui

# CLI (after global install)
mcp-email-server accounts list
mcp-email-server accounts add-oauth2          # interactive OAuth2 setup (M365/Google)
mcp-email-server accounts reauth -a "Account" # re-auth (tries token refresh first, then full flow)
mcp-email-server accounts reauth -a "Account" --force  # skip refresh, force full auth flow
mcp-email-server emails list -a "Account Name" --since "2026-01-01T00:00:00"
mcp-email-server emails read -a "Account Name" <email_id>

# Filter rules
mcp-email-server rules list                        # show all rules
mcp-email-server rules list --account "Account"    # filter by account
mcp-email-server rules apply --dry-run             # preview matches without moving
mcp-email-server rules apply                       # move matched emails
mcp-email-server rules apply --limit 10            # cap emails processed per rule
mcp-email-server rules apply --json                # machine-readable output
mcp-email-server rules add --file ads --name "Ads" --account "Account" --target-folder "Ads" --senders "a@x.com,b@y.com"
mcp-email-server rules add --file alerts --name "Alerts" --account "Account" --target-folder "Alerts" --subjects "Alert,Notification"
mcp-email-server rules add --file junk --name "Junk" --account "Account" --target-folder "Junk" --senders "spam@x.com" --mark-read
mcp-email-server rules add --file work --name "Work Alerts" --account "Account" --target-folder "Alerts" --senders "alerts@sys.com" --subjects "Critical,Down"  # AND match
mcp-email-server rules delete --file ads --name "Ads"

# Flags
mcp-email-server flags add -a "Account" -f "\\Seen" <email_id> ...          # mark as read
mcp-email-server flags remove -a "Account" -f "\\Seen" <email_id> ...       # mark as unread
mcp-email-server flags add -a "Account" -f "\\Flagged" <email_id> ...       # star/flag
mcp-email-server flags remove -a "Account" -f "\\Flagged" <email_id> ...    # unstar/unflag
mcp-email-server flags replace -a "Account" -f "\\Seen" <email_id> ...      # replace all flags

# Folders
mcp-email-server folders list -a "Account"                                   # list all folders
mcp-email-server folders create -a "Account" "FolderName"                    # create a folder
```

## Architecture

The project is both an **MCP server** and a **CLI tool** sharing the same core logic.

### Entry points

- **MCP server**: `mcp_email_server/app.py` — defines all MCP tools using `FastMCP`. Each tool calls `dispatch_handler(account_name)` to get an `EmailHandler`, then delegates.
- **CLI**: `mcp_email_server/cli/__init__.py` — Typer app that registers sub-apps (`accounts`, `emails`, `folders`, `flags`, `rules`) and top-level commands (`stdio`, `sse`, `streamable-http`, `ui`, `reset`). The CLI sub-commands in `cli/emails.py`, etc. call the same underlying handler methods.

### Handler abstraction

- `mcp_email_server/emails/__init__.py` — abstract base class `EmailHandler` defining the contract for all email operations.
- `mcp_email_server/emails/classic.py` — `ClassicEmailHandler`: the only concrete implementation, using `aioimaplib` (IMAP) and `aiosmtplib` (SMTP).
- `mcp_email_server/emails/dispatcher.py` — `dispatch_handler(account_name)` looks up the account in settings and returns the appropriate handler. Currently only `EmailSettings` → `ClassicEmailHandler` is implemented; `ProviderSettings` raises `NotImplementedError`.

### Configuration

- `mcp_email_server/config.py` — Pydantic/pydantic-settings models. Config is stored as TOML at `~/.config/zerolib/mcp_email_server/config.toml` (overridable via `MCP_EMAIL_SERVER_CONFIG_PATH`).
- Environment variables (prefixed `MCP_EMAIL_SERVER_`) take precedence over TOML for account credentials. `Settings.__init__` merges env-sourced accounts at startup.
- `get_settings()` is a module-level singleton; call `get_settings(reload=True)` to force a reload.

### OAuth2 / XOAUTH2 authentication

- `mcp_email_server/oauth2.py` — Token managers for Microsoft 365 (MSAL) and Google. Abstract base `OAuth2TokenManager` with concrete `MSALTokenManager` and `GoogleTokenManager`. Factory: `get_token_manager(provider, client_id, ...)`. The `uses_device_code_flow` property distinguishes the two auth flow types.
- **Config fields** on `EmailSettings`: `auth_type` (`"password"` or `"oauth2"`), `oauth2_provider` (`"microsoft"` or `"google"`), `oauth2_client_id`, `oauth2_tenant_id`, `oauth2_client_secret`.
- **Auth helpers** in `classic.py`: `_imap_authenticate()` and `_smtp_authenticate()` dispatch between `imap.login()` / `smtp.login()` (password) and XOAUTH2 SASL (OAuth2). All 10 login call sites use these helpers.
- **Token cache**: `~/.config/zerolib/mcp_email_server/oauth2_token_cache.json` (MSAL/M365), `google_token_cache.json` (Google). File permissions `0600`.
- **Auth flows** differ by provider:
  - **Microsoft**: Two-step device code flow (`initiate_device_code_flow` → user enters code → `complete_device_code_flow`).
  - **Google**: Single-step browser redirect flow (`run_auth_flow` using `InstalledAppFlow.run_local_server` with `open_browser=False`). Google's device code flow does not support the `https://mail.google.com/` scope.
- **CLI**: `mcp-email-server accounts add-oauth2` — handles both flows automatically based on provider.
- **Reauth**: `accounts reauth` tries `refresh_access_token()` first (uses cached refresh token — no browser/device code needed). Falls back to full auth flow only if refresh fails. `--force` skips the refresh attempt. MCP tool `reauth_oauth2_account` has matching `force` parameter.
- **`refresh_access_token()`**: Base class delegates to `get_access_token()` (works for MSAL which handles refresh internally). `GoogleTokenManager` overrides to always call `credentials.refresh()` regardless of expiry state, since we don't cache the expiry time.
- **Headless environments**: Google's `run_local_server()` requires a localhost redirect, which doesn't work in SSH/Docker. Token refresh via `reauth` (without `--force`) avoids this. For initial auth on headless systems, use SSH port forwarding (`ssh -L 8080:localhost:8080`).
- **MCP tools**: `initiate_oauth2_setup` / `complete_oauth2_setup` — Microsoft uses both steps; Google completes fully in `initiate_oauth2_setup` (no need to call `complete_oauth2_setup`).
- **Cleanup**: `Settings.delete_email()` automatically removes cached OAuth2 tokens.

### Filter rules

- `mcp_email_server/rules.py` — Pydantic models (`Rule`, `RuleFile`, `RuleApplyResult`), TOML file I/O, and `apply_rules()` orchestrator. CLI-only feature (no MCP tools).
- `mcp_email_server/cli/rules.py` — Typer sub-app with `list`, `apply`, `add`, `delete` commands.
- **Rule storage**: TOML files in `~/.config/zerolib/mcp_email_server/rules/` (one or more `*.toml` files, each containing `[[rules]]` entries).
- **Rule format**: Each rule has `name`, `account`, `target_folder`, and at least one of `senders` (IMAP FROM search) or `subjects` (IMAP SUBJECT search). When both are specified, results are AND-intersected (emails must match a sender AND a subject). Optional: `source_mailbox` (default: `"INBOX"`), `mark_read` (default: `false`).
- **Execution**: `apply_rules()` dispatches one handler per rule. `EmailClient.apply_filter_rule()` opens a **single IMAP connection** per rule, runs batched OR queries (25 values/chunk via `_build_or_criteria()`), deduplicates UIDs, then batch-moves (80 UIDs/batch) using MOVE (RFC 6851) with COPY+DELETE fallback. Fail-fast on batch error.
- **Shared helpers**: `EmailClient._search_by_field()` (batched OR search + dedup) and `_move_uids()` (MOVE/COPY+DELETE + expunge) are reused by both `apply_filter_rule` and `move_emails_to_folder`.
- **Path safety**: `_validate_rule_path()` prevents path traversal in file names.
- **`--limit N`**: Cap emails processed per rule; `matched` still reports full search count.
- **`--dry-run`**: Returns matches without moving; no folder creation occurs.
- **`--json`**: Machine-readable output for agent/script consumption (goes to stdout; log noise stays on stderr).

### Key design notes

- Email IDs are IMAP UIDs (strings). Always obtain them from `list_emails_metadata` before calling content/delete/flag operations.
- `delete_emails` is a **hard delete** (sets `\Deleted` flag + `EXPUNGE`) — not a move to Trash. When a user asks to "delete" emails, default to moving them to Trash (`emails move --target-folder Trash`) unless they explicitly request permanent deletion.
- Mailbox names are always quoted via `_quote_mailbox()` for RFC 3501 compatibility with strict servers.
- Body content is truncated to 20,000 characters by default (`MAX_BODY_LENGTH` in `classic.py`); use `truncate_body` param or `save_email_to_file` for full content.
- Attachment download is disabled by default; must be explicitly enabled via `enable_attachment_download = true` in config or env var.

## Linting

Ruff is configured with line length 120, targeting Python 3.10+. `fix = true` auto-fixes on run.
