"""Expunge only the UIDs we marked \\Deleted (UID EXPUNGE), never after a native MOVE."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.classic import EmailClient, _expunge_uids

OK = MagicMock(result="OK", lines=[])


@pytest.fixture
def email_client():
    return EmailClient(EmailServer(user_name="u", password="p", host="imap.example.com", port=993))


def _imap(capabilities=("IMAP4rev1", "UIDPLUS")):
    imap = AsyncMock()
    imap.protocol.capabilities = set(capabilities)
    imap.uid = AsyncMock(return_value=OK)
    imap.expunge = AsyncMock()
    return imap


def _uid_calls(imap, command):
    return [c.args for c in imap.uid.call_args_list if c.args[0] == command]


class TestExpungeUids:
    @pytest.mark.asyncio
    async def test_uses_uid_expunge_with_uidplus(self):
        imap = _imap()
        await _expunge_uids(imap, ["7", "2", "1", "3"])
        assert _uid_calls(imap, "expunge") == [("expunge", "1:3,7")]
        imap.expunge.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_bare_expunge_without_uidplus(self):
        imap = _imap(capabilities=("IMAP4rev1",))
        await _expunge_uids(imap, ["5"])
        imap.expunge.assert_awaited_once()
        assert _uid_calls(imap, "expunge") == []

    @pytest.mark.asyncio
    async def test_nothing_to_expunge(self):
        imap = _imap()
        await _expunge_uids(imap, [])
        imap.uid.assert_not_called()
        imap.expunge.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_uid_expunge_does_not_raise(self):
        imap = _imap()
        imap.uid = AsyncMock(return_value=MagicMock(result="NO", lines=[b"nope"]))
        await _expunge_uids(imap, ["5"])


class TestMoveExpunge:
    @pytest.mark.asyncio
    async def test_native_move_does_not_expunge(self, email_client):
        imap = _imap(capabilities=("IMAP4rev1", "UIDPLUS", "MOVE"))
        moved, failed = await email_client._move_uids(imap, ["1", "2"], "Archive")
        assert (moved, failed) == (["1", "2"], [])
        assert _uid_calls(imap, "expunge") == []
        imap.expunge.assert_not_called()

    @pytest.mark.asyncio
    async def test_copy_fallback_expunges_only_copied_uids(self, email_client):
        imap = _imap()

        async def uid(cmd, *args):
            if cmd == "move":
                if args[0] == "2":
                    raise OSError("MOVE failed")
                return OK
            return OK

        imap.uid = AsyncMock(side_effect=uid)
        moved, failed = await email_client._move_uids(imap, ["1", "2", "3"], "Archive")
        assert (moved, failed) == (["1", "2", "3"], [])
        assert _uid_calls(imap, "expunge") == [("expunge", "2")]
        imap.expunge.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_copy_fallback_expunges_only_batch_uids(self, email_client):
        imap = _imap()

        async def uid(cmd, *args):
            if cmd == "move":
                raise OSError("no MOVE")
            return OK

        imap.uid = AsyncMock(side_effect=uid)
        moved, failed = await email_client._batch_move_uids(imap, ["10", "11", "12"], "Archive")
        assert (moved, failed) == (["10", "11", "12"], [])
        assert _uid_calls(imap, "expunge") == [("expunge", "10:12")]
        imap.expunge.assert_not_called()


class TestDeleteExpunge:
    @pytest.mark.asyncio
    async def test_delete_expunges_only_deleted_uids(self, email_client):
        imap = _imap()
        imap._client_task = asyncio.get_running_loop().create_future()
        imap._client_task.set_result(None)
        imap.login = AsyncMock(return_value=OK)
        email_client.imap_class = MagicMock(return_value=imap)

        deleted, failed = await email_client.delete_emails(["4", "5"], "INBOX")

        assert (deleted, failed) == (["4", "5"], [])
        assert _uid_calls(imap, "expunge") == [("expunge", "4:5")]
        imap.expunge.assert_not_called()
