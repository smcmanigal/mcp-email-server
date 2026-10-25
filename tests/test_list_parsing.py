"""LIST response parsing: quoted/atom/literal names, NIL delimiter, escapes (port of upstream #174/#230)."""

from unittest.mock import AsyncMock

import pytest

from mcp_email_server.emails.classic import _iter_list_entries, _parse_list_line


class TestParseListLine:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ('(\\HasNoChildren) "/" "Sent Items"', (["\\HasNoChildren"], "/", "Sent Items")),
            ('(\\HasNoChildren \\Sent) "/" Sent', (["\\HasNoChildren", "\\Sent"], "/", "Sent")),
            ("(\\Noselect) NIL Shared", (["\\Noselect"], None, "Shared")),
            ('(\\Noselect) NIL "Shared"', (["\\Noselect"], None, "Shared")),
            ('() "." "Say \\"hi\\""', ([], ".", 'Say "hi"')),
            ('() "." "back\\\\slash"', ([], ".", "back\\slash")),
            ('() "/" INBOX', ([], "/", "INBOX")),
            ('() "/" "&AMQ-nderungen"', ([], "/", "&AMQ-nderungen")),
        ],
    )
    def test_parses(self, line, expected):
        assert _parse_list_line(line) == expected

    @pytest.mark.parametrize("line", ["LIST completed", "Success", "", "(\\HasNoChildren"])
    def test_non_entries_ignored(self, line):
        assert _parse_list_line(line) is None


class TestIterListEntries:
    def test_literal_name_is_joined(self):
        lines = [b'(\\HasNoChildren) "/" {7}', bytearray(b'My "Box'), b'() "/" INBOX', b"LIST completed"]
        assert _iter_list_entries(lines) == [(["\\HasNoChildren"], "/", 'My "Box'), ([], "/", "INBOX")]

    def test_str_and_bytes_lines(self):
        assert _iter_list_entries(['() "/" "A"', b'() "/" B']) == [([], "/", "A"), ([], "/", "B")]


class TestConsumers:
    @pytest.mark.asyncio
    async def test_sent_detection_with_atom_name(self):
        from mcp_email_server.config import EmailServer
        from mcp_email_server.emails.classic import EmailClient

        client = EmailClient(EmailServer(user_name="u", password="p", host="h", port=993))
        imap = AsyncMock()
        imap.list = AsyncMock(return_value=("OK", [b'() "/" INBOX', b'(\\HasNoChildren \\Sent) "/" Sent', b"done"]))
        assert await client._find_sent_folder_by_flag(imap) == "Sent"
