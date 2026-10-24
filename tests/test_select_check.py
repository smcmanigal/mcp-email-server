"""SELECT failures surface as a readable error instead of a later state error."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.classic import EmailClient, _select_or_raise

SELECT_NO = MagicMock(result="NO", lines=[b"[NONEXISTENT] Unknown Mailbox: Archve"])


@pytest.fixture
def email_client():
    return EmailClient(EmailServer(user_name="u", password="p", host="imap.example.com", port=993))


async def _connected_imap(select_response):
    imap = AsyncMock()
    imap._client_task = asyncio.get_running_loop().create_future()
    imap._client_task.set_result(None)
    imap.login = AsyncMock(return_value=MagicMock(result="OK", lines=[]))
    imap.select = AsyncMock(return_value=select_response)
    return imap


class TestSelectOrRaise:
    @pytest.mark.asyncio
    async def test_ok_returns_response(self):
        imap = AsyncMock()
        ok = MagicMock(result="OK", lines=[])
        imap.select = AsyncMock(return_value=ok)
        assert await _select_or_raise(imap, "INBOX") is ok
        imap.select.assert_awaited_once_with('"INBOX"')

    @pytest.mark.asyncio
    async def test_no_raises_with_mailbox_and_detail(self):
        imap = AsyncMock()
        imap.select = AsyncMock(return_value=SELECT_NO)
        with pytest.raises(ValueError, match=r"Mailbox 'Archve' could not be selected: NO \(\[NONEXISTENT\]"):
            await _select_or_raise(imap, "Archve")


class TestCallSitesSurfaceSelectFailure:
    @pytest.mark.asyncio
    async def test_get_email_count(self, email_client):
        imap = await _connected_imap(SELECT_NO)
        email_client.imap_class = MagicMock(return_value=imap)
        with pytest.raises(ValueError, match="could not be selected"):
            await email_client.get_email_count(mailbox="Archve")
        imap.uid_search.assert_not_called()
        imap.logout.assert_awaited()

    @pytest.mark.asyncio
    async def test_delete_emails(self, email_client):
        imap = await _connected_imap(SELECT_NO)
        email_client.imap_class = MagicMock(return_value=imap)
        with pytest.raises(ValueError, match="could not be selected"):
            await email_client.delete_emails(["1"], mailbox="Archve")
        imap.uid.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_email_body_by_id(self, email_client):
        imap = await _connected_imap(SELECT_NO)
        email_client.imap_class = MagicMock(return_value=imap)
        with pytest.raises(ValueError, match="could not be selected"):
            await email_client.get_email_body_by_id("1", mailbox="Archve")
