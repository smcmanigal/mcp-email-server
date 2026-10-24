"""IMAP ID (RFC 2971) is sent only when advertised, in the compact form."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_email_server.emails.classic import _send_imap_id


def _imap(capabilities):
    imap = AsyncMock()
    imap.protocol.capabilities = set(capabilities)
    imap.protocol.new_tag = MagicMock(return_value="A001")
    imap.protocol.loop = MagicMock()
    imap.protocol.execute = AsyncMock(return_value=MagicMock(result="OK", lines=[]))
    return imap


@pytest.mark.asyncio
async def test_skipped_when_not_advertised():
    imap = _imap({"IMAP4rev1", "UIDPLUS"})
    await _send_imap_id(imap)
    imap.protocol.execute.assert_not_called()
    imap.id.assert_not_called()


@pytest.mark.asyncio
async def test_sends_compact_form_once_when_advertised():
    imap = _imap({"IMAP4rev1", "ID"})
    await _send_imap_id(imap)
    imap.protocol.execute.assert_awaited_once()
    command = imap.protocol.execute.call_args.args[0]
    assert str(command) == 'A001 ID ("name" "mcp-email-server" "version" "1.0.0")'
    assert command._timeout is not None
    imap.id.assert_not_called()


@pytest.mark.asyncio
async def test_non_ok_response_is_not_fatal():
    imap = _imap({"ID"})
    imap.protocol.execute = AsyncMock(return_value=MagicMock(result="BAD", lines=[b"malformed"]))
    await _send_imap_id(imap)


@pytest.mark.asyncio
async def test_timeout_is_not_fatal():
    imap = _imap({"ID"})
    imap.protocol.execute = AsyncMock(side_effect=TimeoutError())
    await _send_imap_id(imap)
