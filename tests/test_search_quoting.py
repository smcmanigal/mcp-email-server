"""Tests for IMAP search parameter quoting (RFC 3501 Section 9)."""

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
