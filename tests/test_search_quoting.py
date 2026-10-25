"""Tests for IMAP search parameter quoting (RFC 3501 Section 9)."""

import pytest

from mcp_email_server.emails.classic import EmailClient, _quote_search_param


def test_quote_search_param_simple_term():
    """Simple single-word terms are wrapped in double quotes."""
    assert _quote_search_param("hello") == '"hello"'


def test_quote_search_param_multi_word():
    """Multi-word terms are wrapped in double quotes."""
    assert _quote_search_param("hello world") == '"hello world"'


def test_quote_search_param_with_quotes():
    """Double-quote characters inside the term are escaped with backslash."""
    assert _quote_search_param('say "hi"') == '"say \\"hi\\""'


def test_quote_search_param_with_backslashes():
    """Backslash characters inside the term are escaped with backslash."""
    assert _quote_search_param("back\\slash") == '"back\\\\slash"'


def test_build_search_criteria_quotes_all_params():
    """_build_search_criteria applies quoting to all string search parameters."""
    criteria = EmailClient._build_search_criteria(
        subject="multi word subject",
        body="body text",
        text="full text",
        from_address="user@example.com",
        to_address="other@example.com",
    )
    assert criteria[0:2] == ["SUBJECT", '"multi word subject"']
    assert criteria[2:4] == ["BODY", '"body text"']
    assert criteria[4:6] == ["TEXT", '"full text"']
    assert criteria[6:8] == ["FROM", '"user@example.com"']
    assert criteria[8:10] == ["TO", '"other@example.com"']


class TestRejectLineBreaks:
    """A CR/LF inside a quoted string would end the IMAP command (port of upstream #230 part)."""

    @pytest.mark.parametrize("value", ["x\r\nA1 LOGOUT", "line\nbreak", "cr\ronly", "nul\0byte"])
    def test_search_value_rejected(self, value):
        from mcp_email_server.emails.classic import _quote_search_param

        with pytest.raises(ValueError, match="Search value must not contain line breaks"):
            _quote_search_param(value)

    @pytest.mark.parametrize("value", ["INBOX\r\nA1 DELETE INBOX", "a\nb", "nul\0"])
    def test_mailbox_name_rejected(self, value):
        from mcp_email_server.emails.classic import _quote_mailbox

        with pytest.raises(ValueError, match="Mailbox name must not contain line breaks"):
            _quote_mailbox(value)

    def test_tabs_and_unicode_still_allowed(self):
        from mcp_email_server.emails.classic import _quote_mailbox, _quote_search_param

        assert _quote_search_param("a\tb café") == '"a\tb café"'
        assert _quote_mailbox("[Gmail]/Sent Mail") == '"[Gmail]/Sent Mail"'


class TestImapSearchDate:
    def test_english_months_regardless_of_locale(self):
        import locale
        from datetime import datetime

        from mcp_email_server.emails.classic import _imap_search_date

        expected = [
            f"05-{m}-2026" for m in ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        ]
        assert [_imap_search_date(datetime(2026, month, 5)) for month in range(1, 13)] == expected

        original = locale.setlocale(locale.LC_TIME)
        for name in ("de_DE.UTF-8", "fr_FR.UTF-8", "es_ES.UTF-8"):
            try:
                locale.setlocale(locale.LC_TIME, name)
            except locale.Error:
                continue
            try:
                assert _imap_search_date(datetime(2026, 3, 1)) == "01-MAR-2026"
                assert _imap_search_date(datetime(2026, 12, 31)) == "31-DEC-2026"
            finally:
                locale.setlocale(locale.LC_TIME, original)


class TestParseInternalDate:
    def test_parses_with_offset_and_space_padding(self):
        from datetime import datetime, timedelta, timezone

        from mcp_email_server.emails.classic import _parse_internal_date

        assert _parse_internal_date("17-Jul-1996 02:44:25 -0700") == datetime(
            1996, 7, 17, 2, 44, 25, tzinfo=timezone(timedelta(hours=-7))
        )
        assert _parse_internal_date(" 5-Mar-2026 09:00:00 +0530") == datetime(
            2026, 3, 5, 9, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))
        )

    def test_malformed_is_none(self):
        from mcp_email_server.emails.classic import _parse_internal_date

        assert _parse_internal_date("17-Foo-1996 02:44:25 -0700") is None
        assert _parse_internal_date("garbage") is None
