import asyncio
import os
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_email_server.emails.classic import ClassicEmailHandler
from mcp_email_server.emails.models import SaveEmailToFileResponse


def _build_raw_email(
    subject="Test Subject",
    sender="sender@example.com",
    date="Mon, 01 Jan 2024 12:00:00 +0000",
    text_body=None,
    html_body=None,
):
    """Build a raw email bytes object for testing."""
    if html_body and text_body:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
    elif html_body:
        msg = MIMEText(html_body, "html")
    else:
        msg = MIMEText(text_body or "", "plain")

    msg["Subject"] = subject
    msg["From"] = sender
    msg["Date"] = date
    return msg.as_bytes()


def _make_awaitable(value=None):
    """Create a completed future that can be awaited."""
    future = asyncio.get_event_loop().create_future()
    future.set_result(value)
    return future


def _create_mock_imap():
    """Create a mock IMAP object with proper awaitable attributes."""
    mock_imap = AsyncMock()
    mock_imap._client_task = _make_awaitable()
    mock_imap.wait_hello_from_server = AsyncMock()
    mock_imap.login = AsyncMock()
    mock_imap.select = AsyncMock()
    mock_imap.logout = AsyncMock()
    return mock_imap


@pytest.fixture
def mock_handler():
    """Create a ClassicEmailHandler with mocked email settings."""
    with patch("mcp_email_server.emails.classic.ClassicEmailHandler.__init__", return_value=None):
        handler = ClassicEmailHandler.__new__(ClassicEmailHandler)
        handler.incoming_client = MagicMock()
        handler.incoming_client.email_server = MagicMock()
        handler.incoming_client.email_server.host = "imap.example.com"
        handler.incoming_client.email_server.port = 993
        return handler


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test output files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.mark.asyncio
async def test_save_email_markdown_format(mock_handler, temp_dir):
    """Test saving email in markdown format converts HTML to markdown."""
    raw_email = _build_raw_email(
        subject="Newsletter",
        html_body="<h1>Welcome</h1><p>Hello <strong>world</strong></p>",
    )

    file_path = os.path.join(temp_dir, "email.md")

    mock_imap = _create_mock_imap()
    mock_handler.incoming_client.imap_class = MagicMock(return_value=mock_imap)
    mock_handler.incoming_client._fetch_email_with_formats = AsyncMock(
        return_value=[b"1 FETCH (BODY[] {100}", bytearray(raw_email)]
    )
    mock_handler.incoming_client._extract_raw_email = MagicMock(return_value=raw_email)

    result = await mock_handler.save_email_to_file(
        email_id="123",
        file_path=file_path,
        output_format="markdown",
        include_headers=True,
    )

    assert isinstance(result, SaveEmailToFileResponse)
    assert result.email_id == "123"
    assert result.output_format == "markdown"
    assert result.content_length > 0

    with open(file_path) as f:
        content = f.read()
    assert "Subject: Newsletter" in content
    assert "# Welcome" in content
    assert "**world**" in content


@pytest.mark.asyncio
async def test_save_email_html_format(mock_handler, temp_dir):
    """Test saving email in HTML format preserves original HTML."""
    html_content = "<h1>Welcome</h1><p>Hello <strong>world</strong></p>"
    raw_email = _build_raw_email(
        subject="HTML Email",
        html_body=html_content,
    )

    file_path = os.path.join(temp_dir, "email.html")

    mock_imap = _create_mock_imap()
    mock_handler.incoming_client.imap_class = MagicMock(return_value=mock_imap)
    mock_handler.incoming_client._fetch_email_with_formats = AsyncMock(
        return_value=[b"1 FETCH (BODY[] {100}", bytearray(raw_email)]
    )
    mock_handler.incoming_client._extract_raw_email = MagicMock(return_value=raw_email)

    result = await mock_handler.save_email_to_file(
        email_id="456",
        file_path=file_path,
        output_format="html",
        include_headers=True,
    )

    assert result.output_format == "html"
    with open(file_path) as f:
        content = f.read()
    assert "<h1>Welcome</h1>" in content
    assert "<strong>world</strong>" in content


@pytest.mark.asyncio
async def test_save_email_without_headers(mock_handler, temp_dir):
    """Test saving email without headers only writes body content."""
    raw_email = _build_raw_email(
        subject="No Headers",
        text_body="Just the body content.",
    )

    file_path = os.path.join(temp_dir, "email_no_headers.txt")

    mock_imap = _create_mock_imap()
    mock_handler.incoming_client.imap_class = MagicMock(return_value=mock_imap)
    mock_handler.incoming_client._fetch_email_with_formats = AsyncMock(
        return_value=[b"1 FETCH (BODY[] {100}", bytearray(raw_email)]
    )
    mock_handler.incoming_client._extract_raw_email = MagicMock(return_value=raw_email)

    await mock_handler.save_email_to_file(
        email_id="789",
        file_path=file_path,
        output_format="markdown",
        include_headers=False,
    )

    with open(file_path) as f:
        content = f.read()
    assert "Subject:" not in content
    assert "From:" not in content
    assert "---" not in content
    assert "Just the body content." in content


@pytest.mark.asyncio
async def test_save_email_with_headers(mock_handler, temp_dir):
    """Test saving email with headers includes subject, from, date, email_id."""
    raw_email = _build_raw_email(
        subject="Test Headers",
        sender="alice@example.com",
        text_body="Body text.",
    )

    file_path = os.path.join(temp_dir, "email_headers.txt")

    mock_imap = _create_mock_imap()
    mock_handler.incoming_client.imap_class = MagicMock(return_value=mock_imap)
    mock_handler.incoming_client._fetch_email_with_formats = AsyncMock(
        return_value=[b"1 FETCH (BODY[] {100}", bytearray(raw_email)]
    )
    mock_handler.incoming_client._extract_raw_email = MagicMock(return_value=raw_email)

    await mock_handler.save_email_to_file(
        email_id="999",
        file_path=file_path,
        output_format="markdown",
        include_headers=True,
    )

    with open(file_path) as f:
        content = f.read()
    assert "Subject: Test Headers" in content
    assert "From: alice@example.com" in content
    assert "Email-ID: 999" in content
    assert "---" in content
    assert "Body text." in content


@pytest.mark.asyncio
async def test_save_email_plain_text_markdown(mock_handler, temp_dir):
    """Test saving plain text email in markdown format returns text as-is."""
    raw_email = _build_raw_email(
        subject="Plain Email",
        text_body="This is plain text.\nNo HTML here.",
    )

    file_path = os.path.join(temp_dir, "plain.md")

    mock_imap = _create_mock_imap()
    mock_handler.incoming_client.imap_class = MagicMock(return_value=mock_imap)
    mock_handler.incoming_client._fetch_email_with_formats = AsyncMock(
        return_value=[b"1 FETCH (BODY[] {100}", bytearray(raw_email)]
    )
    mock_handler.incoming_client._extract_raw_email = MagicMock(return_value=raw_email)

    await mock_handler.save_email_to_file(
        email_id="100",
        file_path=file_path,
        output_format="markdown",
        include_headers=False,
    )

    with open(file_path) as f:
        content = f.read()
    assert "This is plain text." in content
    assert "No HTML here." in content
