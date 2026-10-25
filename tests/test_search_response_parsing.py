"""UID SEARCH response parsing, including iCloud's completion-only empty result (port of upstream #237).

iCloud omits the untagged ``* SEARCH`` line when nothing matches, so aioimaplib's
first response line is the completion text ("SEARCH completed (took 5 ms)").
That text must read as an empty result, not as UIDs.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.classic import EmailClient, _parse_search_uids

ICLOUD_EMPTY = [b"SEARCH completed (took 5 ms)"]


@pytest.fixture
def email_client():
    server = EmailServer(user_name="u", password="p", host="imap.example.com", port=993, use_ssl=True)
    return EmailClient(server, sender="u@example.com")


def _mock_imap(search_response):
    mock_imap = AsyncMock()
    mock_imap._client_task = asyncio.Future()
    mock_imap._client_task.set_result(None)
    mock_imap.wait_hello_from_server = AsyncMock()
    mock_imap.login = AsyncMock(return_value=MagicMock(result="OK", lines=[]))
    mock_imap.select = AsyncMock()
    mock_imap.uid_search = AsyncMock(return_value=search_response)
    mock_imap.logout = AsyncMock()
    return mock_imap


class TestParseSearchUids:
    @pytest.mark.parametrize(
        ("messages", "expected"),
        [
            ([b"1 2 3", b"SEARCH completed"], ["1", "2", "3"]),
            ([bytearray(b"10 20")], ["10", "20"]),
            (["7 8"], ["7", "8"]),
            ([b""], []),
            ([], []),
            (None, []),
            (ICLOUD_EMPTY, []),
        ],
    )
    def test_parse(self, messages, expected):
        assert _parse_search_uids(messages) == expected

    def test_unexpected_text_yields_no_uids(self):
        """A line mixing words and numbers is never partly trusted as UIDs."""
        assert _parse_search_uids([b"BOGUS 12 34"]) == []

    def test_non_ascii_digits_are_not_uids(self):
        assert _parse_search_uids(["² 3"]) == []


class TestCallSitesWithIcloudEmptyResult:
    @pytest.mark.asyncio
    async def test_metadata_page_total_is_zero(self, email_client):
        mock_imap = _mock_imap(("OK", ICLOUD_EMPTY))
        with patch.object(email_client, "imap_class", return_value=mock_imap):
            assert await email_client.get_emails_metadata_page() == ([], 0)

    @pytest.mark.asyncio
    async def test_metadata_stream_yields_nothing(self, email_client):
        mock_imap = _mock_imap(("OK", ICLOUD_EMPTY))
        with (
            patch.object(email_client, "imap_class", return_value=mock_imap),
            patch.object(email_client, "_batch_fetch_headers") as mock_fetch_headers,
        ):
            emails = [e async for e in email_client.get_emails_metadata_stream(page=1, page_size=10)]

        assert emails == []
        mock_fetch_headers.assert_not_called()

    @pytest.mark.asyncio
    async def test_rule_search_skips_empty_chunk(self, email_client):
        mock_imap = MagicMock()
        mock_imap.uid_search = AsyncMock(side_effect=[("OK", ICLOUD_EMPTY), ("OK", [b"4 9"])])

        result = await email_client._search_by_field(
            mock_imap, "FROM", ["a@x.com", "b@x.com"], since=None, chunk_size=1
        )

        assert result == ["4", "9"]
