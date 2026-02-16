# Rebase Plan: enhanced-mcp-features onto origin/main

## Overview

The upstream `origin/main` has diverged significantly (54 commits) since the `enhanced-mcp-features` branch was created. Many features in the fork have been independently implemented upstream with different (often superior) approaches. This document details a strategy to rebase the fork, keeping unique value while adopting upstream improvements.

**Current state:**
- Fork branch: `enhanced-mcp-features` (13 commits ahead of `fork/main`, including pre-commit fix)
- Upstream: `origin/main` (54 commits ahead of `fork/main`)
- PR #39: Closed without merge (maintainer asked about long-term commitment)

---

## Phase 1: Update fork/main to match origin/main

Sync the fork's main branch with upstream before rebasing the feature branch.

```bash
git checkout main
git pull origin main
git push fork main
```

---

## Phase 2: Create a fresh feature branch from updated main

Rather than attempting a traditional rebase (which will have extensive conflicts due to overlapping features), create a new branch and selectively port features.

```bash
git checkout main
git checkout -b enhanced-mcp-features-v2
```

---

## Phase 3: Drop overlapping features (use upstream's versions)

The following features from the original branch should NOT be ported because upstream has superior implementations.

### 3.1 Unread/Flagged Filtering

**Original fork commits:**
- `917fb7b` feat: Add IMAP flags support for read/unread tracking
- `218abd6` feat: Add unread/flagged filtering to MCP endpoints

**Upstream replacement:** `3e4fea5` Add IMAP flag-based search filters to list_emails_metadata

**Why drop:**
- Upstream uses tri-state parameters (`seen`, `flagged`, `answered` = True/False/None)
- Fork uses limited booleans (`unread_only`, `flagged_only`) that can only filter FOR a state, not AGAINST it
- Upstream includes `answered` filtering; fork does not
- Upstream has comprehensive test coverage for all flag combinations

**Action:** Do not port. Upstream's tri-state filtering is already in main.

---

### 3.2 Email Model Changes (flags, is_read, is_flagged, is_answered in EmailData)

**Original fork commits:**
- `917fb7b` feat: Add IMAP flags support for read/unread tracking
- `06d737c` refactor: Implement unified email fetch method (Phase 2)
- `080f904` refactor: Implement abstract email retrieval (Phase 3)

**Upstream replacement:**
- `49e76a0` refactor: Separate metadata from content (EmailMetadata + EmailBodyResponse)
- `de08972` Batch fetch emails to reduce IMAP round trips
- `fce15ad` refactor: Simplify code and reduce duplication

**Why drop:**
- Upstream separates metadata from content (better for performance)
- Upstream batch-fetches headers (40x faster on large mailboxes: 30+ min to ~2 sec for 25k emails)
- Fork's unified EmailData model always fetches full bodies, even when just browsing
- Upstream includes `message_id` and `recipients` fields for threading support

**Action:** Do not port the model changes or fetch refactoring. Adapt all new features to use upstream's `EmailMetadata`/`EmailBodyResponse` models.

---

### 3.3 Basic Folder Selection (folder parameter on page_email)

**Original fork commit:**
- `82b8f95` feat: Add folder selection and email flag management (folder parameter portion)

**Upstream replacement:**
- `c00b0ab` add optional mailbox parameter and delete_mails tool
- `f64fa61` fix: quote and escape IMAP mailbox names for RFC 3501 compliance
- `4f8b616` Fix: Quote mailbox names and add IMAP flag detection for Sent folder

**Why drop:**
- Upstream uses the correct IMAP term `mailbox` (not `folder`)
- Upstream includes `_quote_mailbox()` helper for RFC 3501 compliance
- Fork's implementation lacks mailbox name escaping (breaks on Proton Bridge, IONOS, and servers with spaces in folder names)
- Upstream's implementation is tested across Gmail, Proton Bridge, and IONOS

**Action:** Do not port. Use upstream's `mailbox` parameter and `_quote_mailbox()` helper.

---

### 3.4 IMAP Search Quoting (partial)

**Original fork commit:**
- `f07dc5e` feat: Add file logging and fix IMAP search with quotes

**Upstream replacement:**
- `f64fa61` fix: quote and escape IMAP mailbox names for RFC 3501 compliance

**Why partially drop:**
- The fork and upstream solve DIFFERENT quoting problems:
  - Fork: quotes search parameters (e.g., `SUBJECT "meeting notes"`)
  - Upstream: quotes mailbox names (e.g., `SELECT "INBOX"`)
- Fork's search parameter quoting lacks proper escaping (breaks if search term contains quotes)
- Upstream's `_quote_mailbox()` includes proper RFC 3501 Section 9 escaping

**Action:** Drop the raw quoting. Re-implement search parameter quoting using upstream's escaping pattern (see Phase 4.5).

---

### 3.5 File Logging with Rotation

**Original fork commit:**
- `f07dc5e` feat: Add file logging and fix IMAP search with quotes (logging portion)

**Upstream replacement:** Upstream already has an identical implementation in `mcp_email_server/log.py` using the same environment variables and loguru configuration.

**Why drop:**
- Upstream uses the same env vars: `MCP_EMAIL_SERVER_LOG_FILE`, `MCP_EMAIL_SERVER_LOG_ROTATION`, `MCP_EMAIL_SERVER_LOG_RETENTION`
- Upstream has the same loguru setup with compression, rotation, and retention
- The implementations are functionally identical
- No unique value in the fork's version

**Action:** Do not port. Upstream already has this feature.

---

## Phase 4: Port unique features to the new branch

### Upstream features to preserve

When porting fork features onto the new branch, take care NOT to break or regress any of these upstream features that the fork currently lacks. All new code must coexist with:

1. **Two-phase metadata/content API** — `list_emails_metadata` (fast, headers only) + `get_emails_content` (fetch bodies on demand)
2. **Batch header fetching** — `_batch_fetch_dates()` and `_batch_fetch_headers()` for 40x performance on large mailboxes
3. **Email threading** — `message_id` field on `EmailMetadata`, `in_reply_to` and `references` parameters on `send_email`
4. **Recipients field** — `recipients: list[str]` on `EmailMetadata`
5. **Attachment download** — `download_attachment()` tool with security validation
6. **HTML email sending** — `html: bool` and `attachments: list[str]` on `send_email`
7. **Environment variable configuration** — `EmailSettings.from_env()` for deployment-friendly config
8. **Save-to-sent folder** — `save_to_sent` config, `append_to_sent()`, `_find_sent_folder_by_flag()`
9. **SSL certificate verification** — `verify_ssl` config option, `_create_smtp_ssl_context()`
10. **Delete emails** — `delete_emails` tool
11. **IMAP ID command** — `_send_imap_id()` helper for server compatibility
12. **Mailbox quoting** — `_quote_mailbox()` for RFC 3501 compliance

All new methods should use upstream's helpers (`_quote_mailbox()`, `_send_imap_id()`) and follow upstream's naming conventions (`email_id`, `mailbox`, `seen`/`flagged`/`answered`).

### Features to port

The following features have no upstream equivalent and should be ported, adapted to work with upstream's architecture.

### 4.1 Flag Management Tools (add/remove/replace)

**Original fork commit:**
- `82b8f95` feat: Add folder selection and email flag management (flag management portion)
- `df21ad1` fix: Resolve flag parsing regression and add comprehensive test coverage

**Files to create/modify:**
- `mcp_email_server/emails/classic.py` — Add `add_flags()`, `remove_flags()`, `replace_flags()` methods
- `mcp_email_server/emails/dispatcher.py` — Add dispatcher methods
- `mcp_email_server/emails/provider/__init__.py` — Add to EmailHandler abstract class
- MCP tool registration file — Add `add_email_flags`, `remove_email_flags`, `replace_email_flags` tools

**Adaptation required:**
- Use upstream's `_quote_mailbox()` when selecting the mailbox for flag operations
- Use upstream's connection patterns (or introduce context manager, see 4.6)
- Use upstream's `email_id` field name instead of `uid`
- Add proper error handling consistent with upstream patterns
- Ensure flag normalization handles both bytes and strings (the bug fix from `df21ad1`)

**Tests to port/create:**
- `test_extract_email_and_flags_with_bytes` — Critical bug fix test
- `test_extract_email_and_flags_with_strings`
- `test_extract_email_and_flags_no_flags`
- `test_normalize_flags`
- `test_add_flags` / `test_remove_flags` / `test_replace_flags`
- Integration tests for MCP tool endpoints

---

### 4.2 Folder Listing Tool

**Original fork commit:**
- `4443454` feat: Add move-to-folder functionality with UID tracking (list_folders portion)

**Files to create/modify:**
- `mcp_email_server/emails/classic.py` — Add `list_folders()` method
- `mcp_email_server/emails/dispatcher.py` — Add dispatcher method
- `mcp_email_server/emails/provider/__init__.py` — Add to abstract class
- MCP tool registration — Add `list_email_folders` tool

**Adaptation required:**
- Use `_quote_mailbox()` if any mailbox selection is needed
- Return structured data (folder name, flags, delimiter)
- Support pattern parameter for filtering
- Follow upstream's naming conventions

**Tests to create:**
- `test_list_folders_returns_expected_structure`
- `test_list_folders_with_pattern_filter`
- `test_list_folders_empty_account`

---

### 4.3 Move-to-Folder Tool

**Original fork commit:**
- `4443454` feat: Add move-to-folder functionality with UID tracking
- `2d30f35` refactor: Consolidate move functions for API consistency

**Files to create/modify:**
- `mcp_email_server/emails/classic.py` — Add `move_emails_to_folder()` method
- `mcp_email_server/emails/dispatcher.py` — Add dispatcher method
- `mcp_email_server/emails/provider/__init__.py` — Add to abstract class
- MCP tool registration — Add `move_emails_to_folder` tool

**Adaptation required:**
- Use `_quote_mailbox()` for both source and target mailbox names
- Accept `email_id` (upstream naming) instead of `uid`
- Support `create_if_missing` parameter for auto-folder creation
- Implement MOVE command with COPY+DELETE fallback for older servers
- Return structured results with success/failure counts
- Coordinate with upstream's existing `delete_emails` tool (avoid duplication)

**Tests to create:**
- `test_move_emails_to_folder_with_move_command`
- `test_move_emails_to_folder_copy_delete_fallback`
- `test_move_emails_to_folder_create_missing`
- `test_move_emails_to_folder_invalid_uid`
- `test_move_emails_batch_partial_failure`

---

### 4.4 Save Email to File Tool

**Original fork commit:**
- `7e6d1e3` feat: Add email truncation and save-to-file functionality

**Files to create/modify:**
- MCP tool registration — Add `save_email_to_file` tool
- `mcp_email_server/emails/classic.py` — Add `get_email_by_uid()` method (full content, no truncation)
- `mcp_email_server/utils/html_converter.py` — HTML-to-markdown conversion utility (new file)

**Adaptation required:**
- Use upstream's `get_emails_content()` to fetch the full email body (or add a single-email variant)
- Use upstream's `email_id` field name
- Use `_quote_mailbox()` for mailbox selection
- Support both HTML and markdown output formats
- Include optional headers (subject, from, date, email_id, body_format)
- Return metadata: success status, file path, content length

**HTML-to-markdown converter details:**
- Port `html_converter.py` as a new utility module
- Handles: headers, links, images, bold/italic, lists
- Removes: tracking pixels, CSS, scripts
- Cleans up special characters and whitespace
- Extracts plain text from multipart emails

**Tests to create:**
- `test_save_email_to_file_markdown_format`
- `test_save_email_to_file_html_format`
- `test_save_email_to_file_with_headers`
- `test_save_email_to_file_without_headers`
- `test_save_email_to_file_nonexistent_uid`
- `test_html_to_markdown_basic_conversion`
- `test_html_to_markdown_removes_tracking_pixels`
- `test_html_to_markdown_preserves_links`

---

### 4.5 Search Parameter Quoting (re-implemented)

**Original fork commit:**
- `f07dc5e` feat: Add file logging and fix IMAP search with quotes (search quoting portion)

**Files to modify:**
- `mcp_email_server/emails/classic.py` — Add or modify search criteria builder

**Re-implementation approach:**
Instead of the fork's naive quoting (`f'"{subject}"'`), create a proper helper modeled on upstream's `_quote_mailbox()`:

```python
def _quote_search_param(param: str) -> str:
    """Quote and escape IMAP search parameter per RFC 3501 Section 9."""
    escaped = param.replace("\\", "\\\\").replace('"', r'\"')
    return f'"{escaped}"'
```

Apply to all search parameters: SUBJECT, BODY, TEXT, FROM, TO.

**Tests to create:**
- `test_quote_search_param_simple_term`
- `test_quote_search_param_multi_word`
- `test_quote_search_param_with_quotes`
- `test_quote_search_param_with_backslashes`
- `test_search_criteria_uses_quoted_params`

---

### 4.6 IMAP Context Manager

**Original fork commit:**
- `ad120d0` refactor: Implement IMAP context manager (Phase 1)

**Files to modify:**
- `mcp_email_server/emails/classic.py` — Add `imap_connection()` async context manager

**Implementation:**
```python
@asynccontextmanager
async def imap_connection(self, select_mailbox: str = "INBOX"):
    imap = self.imap_class(...)
    try:
        await imap._client_task
        await imap.wait_hello_from_server()
        await imap.login(...)
        await _send_imap_id(imap)  # Use upstream's helper
        if select_mailbox:
            await imap.select(_quote_mailbox(select_mailbox))
        yield imap
    finally:
        await imap.logout()
```

**Adaptation required:**
- Use upstream's `_send_imap_id()` helper instead of inline ID command
- Use `_quote_mailbox()` for mailbox selection
- Rename parameter from `select_folder` to `select_mailbox`
- Apply to NEW methods only (flag management, folder listing, move-to-folder, save-to-file)
- Do NOT refactor upstream's existing methods to use the context manager (keep diff minimal)

**Tests to create:**
- `test_imap_connection_context_manager_connects`
- `test_imap_connection_context_manager_cleanup_on_error`
- `test_imap_connection_selects_mailbox`

---

### 4.7 User-Configurable Body Truncation

**Original fork commit:**
- `7e6d1e3` feat: Add email truncation and save-to-file functionality (truncation portion)

**Context:**
Upstream already has truncation logic — hardcoded at 20,000 characters (`MAX_BODY_LENGTH` constant) with a `TODO: Allow retrieving full email body` comment. The fork's unique contribution is making this user-configurable via a `truncate_body` parameter. The truncation infrastructure exists; only the parameter exposure is new.

**Files to modify:**
- MCP tool registration — Add `truncate_body` parameter to `get_emails_content` tool
- `mcp_email_server/emails/classic.py` — Replace hardcoded `MAX_BODY_LENGTH` with parameter, keeping 20,000 as default

**Adaptation required:**
- Integrate with upstream's two-step API:
  - `list_emails_metadata` — No truncation needed (no body returned)
  - `get_emails_content` — Add `truncate_body` parameter here
- Keep upstream's 20,000 default but allow override via parameter
- Preserve upstream's existing `"...[TRUNCATED]"` indicator
- Consider adding `is_truncated` and `original_length` fields to response model

**Tests to create:**
- `test_truncate_body_at_limit`
- `test_truncate_body_below_limit`
- `test_truncate_body_none_returns_full`
- `test_truncate_body_default_20000`

---

## Phase 5: Integration testing

After porting all features, run the full test suite and verify:

```bash
make check          # Linting and formatting
make test           # Full test suite
```

**Checklist:**
- [ ] All upstream tests still pass
- [ ] All new tests pass
- [ ] `_quote_mailbox()` used in ALL IMAP SELECT calls (including new methods)
- [ ] `_quote_search_param()` used for all search criteria
- [ ] No references to old `folder` parameter (use `mailbox`)
- [ ] No references to old `uid` field name in MCP tools (use `email_id`)
- [ ] No references to old `unread_only`/`flagged_only` params (use `seen`/`flagged`)
- [ ] Flag management tools handle both bytes and string IMAP responses
- [ ] Context manager properly cleans up on exceptions
- [ ] HTML-to-markdown converter handles edge cases (empty body, plain text, malformed HTML)
- [ ] All upstream features preserved (threading, attachments, batch fetch, etc. — see Phase 4 preamble)

---

## Phase 6: Submit new PR

### PR Scope (features NOT in upstream)

1. Flag management tools (add/remove/replace email flags)
2. Folder listing tool
3. Move-to-folder tool
4. Save email to file tool
5. HTML-to-markdown conversion
6. User-configurable body truncation (parameter for existing upstream logic)
7. IMAP search parameter quoting (with proper escaping)
8. IMAP context manager for new methods

### PR Description Template

```
## Summary
Add email management tools (flag operations, folder listing, move-to-folder,
save-to-file) and infrastructure improvements (configurable truncation,
file logging, IMAP context manager).

## New MCP Tools
- `add_email_flags` — Add flags to emails (system or custom)
- `remove_email_flags` — Remove flags from emails
- `replace_email_flags` — Replace all flags on emails
- `list_email_folders` — List available IMAP folders
- `move_emails_to_folder` — Move emails between folders (with auto-create)
- `save_email_to_file` — Export full email to file (HTML or markdown)

## Enhancements
- User-configurable `truncate_body` parameter (replaces hardcoded 20k limit)
- IMAP search parameter quoting with RFC 3501 escaping
- IMAP async context manager for cleaner connection handling

## Test Coverage
- XX new tests covering all new functionality
- Flag parsing edge cases (bytes vs strings)
- Folder operations (list, move, create)
- Search parameter escaping
- HTML-to-markdown conversion
```

### Addressing Maintainer's Concern

The original PR #39 was closed because the maintainer asked about long-term commitment. Consider:
- Responding on PR #39 expressing willingness to maintain
- Breaking the PR into smaller, focused PRs (e.g., one for flag management, one for folder ops)
- Offering to help review other PRs and fix issues

---

## File Reference: What Goes Where

| Feature | Primary File(s) | Test File(s) |
|---|---|---|
| Flag management | `emails/classic.py`, `emails/dispatcher.py`, `emails/provider/__init__.py`, MCP tools | `tests/test_flag_management.py` |
| Folder listing | `emails/classic.py`, `emails/dispatcher.py`, MCP tools | `tests/test_folder_operations.py` |
| Move-to-folder | `emails/classic.py`, `emails/dispatcher.py`, MCP tools | `tests/test_folder_operations.py` |
| Save-to-file | MCP tools, `emails/classic.py` | `tests/test_save_email.py` |
| HTML converter | `utils/html_converter.py` (new) | `tests/test_html_converter.py` |
| Search quoting | `emails/classic.py` | `tests/test_search_quoting.py` |
| Context manager | `emails/classic.py` | `tests/test_imap_connection.py` |
| Configurable truncation | MCP tools, `emails/classic.py` | `tests/test_truncation.py` |
