import pytest

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.classic import MAX_BODY_LENGTH, EmailClient


@pytest.fixture
def email_client():
    server = EmailServer(
        user_name="test_user",
        password="test_password",
        host="imap.example.com",
        port=993,
        use_ssl=True,
    )
    return EmailClient(server)


def _make_raw_email(body_text: str) -> bytes:
    """Build a minimal RFC 822 message with the given plain-text body."""
    return (
        b"From: sender@example.com\r\n"
        b"To: recipient@example.com\r\n"
        b"Subject: Test\r\n"
        b"Date: Mon, 20 Jan 2025 10:30:00 +0000\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n" + body_text.encode("utf-8")
    )


class TestTruncateBody:
    def test_truncate_body_at_limit(self, email_client):
        """Body exactly at the limit should NOT be truncated."""
        body_text = "x" * MAX_BODY_LENGTH
        raw = _make_raw_email(body_text)
        result = email_client._parse_email_data(raw, "1")
        assert result["body"] == body_text
        assert "TRUNCATED" not in result["body"]

    def test_truncate_body_below_limit(self, email_client):
        """Body shorter than the limit should NOT be truncated."""
        body_text = "short body"
        raw = _make_raw_email(body_text)
        result = email_client._parse_email_data(raw, "1")
        assert result["body"] == body_text
        assert "TRUNCATED" not in result["body"]

    def test_truncate_body_none_uses_default(self, email_client):
        """When truncate_body is None, the default MAX_BODY_LENGTH is used."""
        body_text = "x" * (MAX_BODY_LENGTH + 100)
        raw = _make_raw_email(body_text)
        result = email_client._parse_email_data(raw, "1", truncate_body=None)
        assert result["body"] == "x" * MAX_BODY_LENGTH + "...[TRUNCATED]"

    def test_truncate_body_custom_value(self, email_client):
        """When truncate_body is provided, it overrides the default limit."""
        body_text = "x" * 200
        raw = _make_raw_email(body_text)
        result = email_client._parse_email_data(raw, "1", truncate_body=50)
        assert result["body"] == "x" * 50 + "...[TRUNCATED]"


class TestBodyOffset:
    BODY = "".join(chr(ord("a") + i % 26) for i in range(250))

    def test_pages_cover_body_exactly(self, email_client):
        """Paging by offset += truncate_body reassembles the whole body; only the last page lacks the marker."""
        raw = _make_raw_email(self.BODY)
        pages = [
            email_client._parse_email_data(raw, "1", truncate_body=100, body_offset=o)["body"] for o in (0, 100, 200)
        ]
        assert [p.endswith("...[TRUNCATED]") for p in pages] == [True, True, False]
        assert "".join(p.removesuffix("...[TRUNCATED]") for p in pages) == self.BODY

    def test_offset_with_default_limit(self, email_client):
        """A short body read from an offset returns the rest without a marker."""
        result = email_client._parse_email_data(_make_raw_email(self.BODY), "1", body_offset=240)
        assert result["body"] == self.BODY[240:]

    def test_offset_past_end_returns_empty(self, email_client):
        result = email_client._parse_email_data(_make_raw_email(self.BODY), "1", body_offset=1000)
        assert result["body"] == ""

    def test_page_ending_at_body_end_has_no_marker(self, email_client):
        result = email_client._parse_email_data(_make_raw_email(self.BODY), "1", truncate_body=50, body_offset=200)
        assert result["body"] == self.BODY[200:]
