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
