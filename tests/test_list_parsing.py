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


class TestModifiedUtf7:
    @pytest.mark.parametrize(
        ("decoded", "encoded"),
        [
            ("~peter/mail/台北/日本語", "~peter/mail/&U,BTFw-/&ZeVnLIqe-"),  # RFC 3501 5.1.3 example
            ("Änderungen", "&AMQ-nderungen"),
            ("Entwürfe", "Entw&APw-rfe"),
            ("Tom & Jerry", "Tom &- Jerry"),
            ("😀 emoji", "&2D3eAA- emoji"),
            ("INBOX", "INBOX"),
        ],
    )
    def test_round_trip(self, decoded, encoded):
        from mcp_email_server.emails.classic import _decode_modified_utf7, _encode_modified_utf7

        assert _encode_modified_utf7(decoded) == encoded
        assert _decode_modified_utf7(encoded) == decoded

    def test_quote_mailbox_encodes_decoded_names(self):
        from mcp_email_server.emails.classic import _quote_mailbox

        assert _quote_mailbox("Entwürfe") == '"Entw&APw-rfe"'
        assert _quote_mailbox("Tom & Jerry") == '"Tom &- Jerry"'

    def test_already_encoded_name_is_not_double_encoded(self):
        from mcp_email_server.emails.classic import _quote_mailbox

        assert _quote_mailbox("&AMQ-nderungen") == '"&AMQ-nderungen"'
        assert _quote_mailbox("Tom &- Jerry") == '"Tom &- Jerry"'

    def test_display_decoding_leaves_invalid_names_alone(self):
        from mcp_email_server.emails.classic import _decode_mailbox_name

        assert _decode_mailbox_name("&AMQ-nderungen") == "Änderungen"
        assert _decode_mailbox_name("R&D") == "R&D"
        assert _decode_mailbox_name("bad &!!- seq") == "bad &!!- seq"

    @pytest.mark.asyncio
    async def test_list_folders_and_sent_detection_decode(self):
        from contextlib import asynccontextmanager

        from mcp_email_server.config import EmailServer, EmailSettings
        from mcp_email_server.emails.classic import ClassicEmailHandler, EmailClient

        lines = [b'(\\HasNoChildren) "/" "&AMQ-nderungen"', b'(\\HasNoChildren \\Sent) "/" "Gesendete Elemente"']
        imap = AsyncMock()
        imap.list = AsyncMock(return_value=("OK", [*lines, b"done"]))

        server = EmailServer(user_name="u", password="p", host="h", port=993)
        settings = EmailSettings(
            account_name="a", full_name="A", email_address="a@x.com", incoming=server, outgoing=server
        )
        handler = ClassicEmailHandler(settings)

        @asynccontextmanager
        async def _conn(select_mailbox="INBOX"):
            yield imap

        handler.incoming_client.imap_connection = _conn
        handler.imap_connection = _conn
        folders = await handler.list_folders()
        assert [f["name"] for f in folders] == ["Änderungen", "Gesendete Elemente"]

        imap.list = AsyncMock(return_value=("OK", [b'(\\Sent) "/" "Entw&APw-rfe"']))
        assert await EmailClient(server)._find_sent_folder_by_flag(imap) == "Entwürfe"
