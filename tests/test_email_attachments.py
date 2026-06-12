"""Test email attachment functionality."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.classic import EmailClient


@pytest.fixture
def email_server():
    return EmailServer(
        user_name="test_user",
        password="test_password",
        host="smtp.example.com",
        port=465,
        use_ssl=True,
    )


@pytest.fixture
def email_client(email_server):
    return EmailClient(email_server, sender="Test User <test@example.com>")


class TestEmailAttachments:
    @pytest.mark.asyncio
    async def test_send_email_with_single_attachment(self, email_client, tmp_path):
        """Test sending email with a single attachment."""
        # Create a test file
        test_file = tmp_path / "document.pdf"
        test_file.write_bytes(b"PDF content here")

        # Mock SMTP
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock()

        with patch("mcp_email_server.emails.classic.aiosmtplib.SMTP", return_value=mock_smtp):
            await email_client.send_email(
                recipients=["recipient@example.com"],
                subject="Test with attachment",
                body="Please see attached file",
                attachments=[str(test_file)],
            )

            # Verify SMTP methods were called
            mock_smtp.login.assert_called_once()
            mock_smtp.send_message.assert_called_once()

            # Get the message that was sent
            call_args = mock_smtp.send_message.call_args
            message = call_args[0][0]

            # Verify message is multipart (required for attachments)
            assert message.is_multipart()
            assert "document.pdf" in str(message)

    @pytest.mark.asyncio
    async def test_send_email_with_multiple_attachments(self, email_client, tmp_path):
        """Test sending email with multiple attachments."""
        # Create multiple test files
        file1 = tmp_path / "document1.pdf"
        file1.write_bytes(b"PDF content 1")

        file2 = tmp_path / "image.png"
        file2.write_bytes(b"PNG content")

        file3 = tmp_path / "data.csv"
        file3.write_text("col1,col2\nval1,val2")

        # Mock SMTP
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock()

        with patch("mcp_email_server.emails.classic.aiosmtplib.SMTP", return_value=mock_smtp):
            await email_client.send_email(
                recipients=["recipient@example.com"],
                subject="Test with multiple attachments",
                body="Please see attached files",
                attachments=[str(file1), str(file2), str(file3)],
            )

            mock_smtp.send_message.assert_called_once()
            message = mock_smtp.send_message.call_args[0][0]

            assert message.is_multipart()
            message_str = str(message)
            assert "document1.pdf" in message_str
            assert "image.png" in message_str
            assert "data.csv" in message_str

    @pytest.mark.asyncio
    async def test_send_email_without_attachments(self, email_client):
        """Test sending email without attachments (backward compatibility)."""
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock()

        with patch("mcp_email_server.emails.classic.aiosmtplib.SMTP", return_value=mock_smtp):
            await email_client.send_email(
                recipients=["recipient@example.com"],
                subject="Test without attachment",
                body="Simple email",
            )

            mock_smtp.send_message.assert_called_once()
            message = mock_smtp.send_message.call_args[0][0]

            # Without attachments, message should not be multipart
            assert not message.is_multipart()

    @pytest.mark.asyncio
    async def test_send_email_attachment_file_not_found(self, email_client):
        """Test error handling when attachment file doesn't exist."""
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock()

        with patch("mcp_email_server.emails.classic.aiosmtplib.SMTP", return_value=mock_smtp):
            with pytest.raises(FileNotFoundError, match="Attachment file not found"):
                await email_client.send_email(
                    recipients=["recipient@example.com"],
                    subject="Test",
                    body="Test",
                    attachments=["/nonexistent/file.pdf"],
                )

    @pytest.mark.asyncio
    async def test_send_email_attachment_is_directory(self, email_client, tmp_path):
        """Test error handling when attachment path is a directory."""
        # Create a directory
        test_dir = tmp_path / "test_directory"
        test_dir.mkdir()

        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock()

        with patch("mcp_email_server.emails.classic.aiosmtplib.SMTP", return_value=mock_smtp):
            with pytest.raises(ValueError, match="Attachment path is not a file"):
                await email_client.send_email(
                    recipients=["recipient@example.com"],
                    subject="Test",
                    body="Test",
                    attachments=[str(test_dir)],
                )

    @pytest.mark.asyncio
    async def test_send_email_html_with_attachments(self, email_client, tmp_path):
        """Test sending HTML email with attachments."""
        test_file = tmp_path / "report.pdf"
        test_file.write_bytes(b"Report content")

        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock()

        with patch("mcp_email_server.emails.classic.aiosmtplib.SMTP", return_value=mock_smtp):
            await email_client.send_email(
                recipients=["recipient@example.com"],
                subject="HTML email with attachment",
                body="<h1>Report</h1><p>See attached</p>",
                html=True,
                attachments=[str(test_file)],
            )

            mock_smtp.send_message.assert_called_once()
            message = mock_smtp.send_message.call_args[0][0]

            assert message.is_multipart()
            assert "report.pdf" in str(message)

    @pytest.mark.asyncio
    async def test_mime_type_detection(self, email_client, tmp_path):
        """Test MIME type detection for different file types."""
        # Create files with different extensions
        files = {
            "document.pdf": b"PDF",
            "image.jpg": b"JPEG",
            "data.json": b'{"key": "value"}',
            "archive.zip": b"ZIP",
            "text.txt": b"Text",
        }

        test_files = []
        for filename, content in files.items():
            file_path = tmp_path / filename
            file_path.write_bytes(content)
            test_files.append(str(file_path))

        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock()

        with patch("mcp_email_server.emails.classic.aiosmtplib.SMTP", return_value=mock_smtp):
            await email_client.send_email(
                recipients=["recipient@example.com"],
                subject="Test MIME types",
                body="Various file types",
                attachments=test_files,
            )

            mock_smtp.send_message.assert_called_once()
            message = mock_smtp.send_message.call_args[0][0]

            # Verify all files are in the message
            message_str = str(message)
            for filename in files:
                assert filename in message_str


class TestDownloadAttachmentMailboxParam:
    """Tests for download_attachment mailbox parameter."""

    @pytest.mark.asyncio
    async def test_download_attachment_default_mailbox(self, email_client, tmp_path):
        """Test download_attachment uses INBOX by default."""
        import asyncio

        save_path = str(tmp_path / "attachment.pdf")

        mock_imap = AsyncMock()
        mock_imap._client_task = asyncio.Future()
        mock_imap._client_task.set_result(None)
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock(return_value=MagicMock(result="OK", lines=[]))
        mock_imap.select = AsyncMock(return_value=("OK", [b"1"]))
        mock_imap.logout = AsyncMock()

        # Mock _fetch_email_with_formats to return None (will raise ValueError)
        with patch.object(email_client, "_fetch_email_with_formats", return_value=None):
            with patch.object(email_client, "imap_class", return_value=mock_imap):
                with pytest.raises(ValueError):
                    await email_client.download_attachment(
                        email_id="123",
                        attachment_name="document.pdf",
                        save_path=save_path,
                    )

                # Verify select was called with quoted INBOX
                mock_imap.select.assert_called_once_with('"INBOX"')

    @pytest.mark.asyncio
    async def test_download_attachment_custom_mailbox(self, email_client, tmp_path):
        """Test download_attachment with custom mailbox parameter."""
        import asyncio

        save_path = str(tmp_path / "attachment.pdf")

        mock_imap = AsyncMock()
        mock_imap._client_task = asyncio.Future()
        mock_imap._client_task.set_result(None)
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock(return_value=MagicMock(result="OK", lines=[]))
        mock_imap.select = AsyncMock(return_value=("OK", [b"1"]))
        mock_imap.logout = AsyncMock()

        with patch.object(email_client, "_fetch_email_with_formats", return_value=None):
            with patch.object(email_client, "imap_class", return_value=mock_imap):
                with pytest.raises(ValueError):
                    await email_client.download_attachment(
                        email_id="123",
                        attachment_name="document.pdf",
                        save_path=save_path,
                        mailbox="All Mail",
                    )

                # Verify select was called with quoted custom mailbox
                mock_imap.select.assert_called_once_with('"All Mail"')

    @pytest.mark.asyncio
    async def test_download_attachment_special_folder(self, email_client, tmp_path):
        """Test download_attachment with special folder like [Gmail]/Sent Mail."""
        import asyncio

        save_path = str(tmp_path / "attachment.pdf")

        mock_imap = AsyncMock()
        mock_imap._client_task = asyncio.Future()
        mock_imap._client_task.set_result(None)
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock(return_value=MagicMock(result="OK", lines=[]))
        mock_imap.select = AsyncMock(return_value=("OK", [b"1"]))
        mock_imap.logout = AsyncMock()

        with patch.object(email_client, "_fetch_email_with_formats", return_value=None):
            with patch.object(email_client, "imap_class", return_value=mock_imap):
                with pytest.raises(ValueError):
                    await email_client.download_attachment(
                        email_id="123",
                        attachment_name="document.pdf",
                        save_path=save_path,
                        mailbox="[Gmail]/Sent Mail",
                    )

                # Verify select was called with quoted special folder
                mock_imap.select.assert_called_once_with('"[Gmail]/Sent Mail"')


class TestUnicodeAttachmentNameMatching:
    """Tests for NFC normalization in download_attachment matching (upstream #168).

    macOS mail clients typically emit NFD-encoded filenames in MIME headers
    while keyboards and most other systems produce NFC. The forms display
    identically but fail exact ``==`` comparison.
    """

    def test_normalize_attachment_name_bridges_nfc_nfd(self):
        import unicodedata

        nfc = "Análisis.xlsx"
        nfd = unicodedata.normalize("NFD", nfc)
        assert nfc != nfd  # sanity: the raw strings differ
        assert EmailClient._normalize_attachment_name(nfc) == EmailClient._normalize_attachment_name(nfd)

    @staticmethod
    def _build_email_with_unicode_attachment(filename: str, payload: bytes = b"spreadsheet bytes") -> bytes:
        from email.mime.application import MIMEApplication
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("mixed")
        msg["Subject"] = "Unicode filename"
        msg["From"] = "sender@example.com"
        msg["To"] = "recipient@example.com"
        msg["Date"] = "Fri, 8 May 2026 19:17:09 +0200"
        msg.attach(MIMEText("Please see attached spreadsheet.", "plain", "utf-8"))

        part = MIMEApplication(payload, _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
        return msg.as_bytes()

    def test_parse_exposes_decoded_unicode_filename(self, email_client):
        """Non-ASCII attachment filenames are listed as decoded strings."""
        filename = "Actividades operacionales bienal MC rev 1 2 - con Análisis 1.xlsx"
        raw_email = self._build_email_with_unicode_attachment(filename)

        result = email_client._parse_email_data(raw_email, email_id="44")

        assert result["attachments"] == [filename]

    @pytest.mark.asyncio
    async def test_download_with_nfd_request_name_succeeds(self, email_client, tmp_path):
        """download_attachment matches when the requested name is NFD but the header is NFC."""
        import asyncio
        import unicodedata

        filename = "Análisis 1.xlsx"
        raw_email = self._build_email_with_unicode_attachment(filename)
        save_path = tmp_path / "analysis.xlsx"

        mock_imap = AsyncMock()
        mock_imap._client_task = asyncio.Future()
        mock_imap._client_task.set_result(None)
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock(return_value=MagicMock(result="OK", lines=[]))
        mock_imap.select = AsyncMock(return_value=("OK", [b"1"]))
        mock_imap.logout = AsyncMock()

        fetch_data = [b"1 FETCH (BODY[] {%d}" % len(raw_email), bytearray(raw_email), b")"]
        with patch.object(email_client, "_fetch_email_with_formats", return_value=fetch_data):
            with patch.object(email_client, "imap_class", return_value=mock_imap):
                result = await email_client.download_attachment(
                    email_id="1",
                    attachment_name=unicodedata.normalize("NFD", filename),
                    save_path=str(save_path),
                )

        assert result["mime_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert save_path.read_bytes() == b"spreadsheet bytes"

    @pytest.mark.asyncio
    async def test_download_unmatched_name_still_raises(self, email_client, tmp_path):
        """Normalization must not loosen matching: different names still fail."""
        import asyncio

        raw_email = self._build_email_with_unicode_attachment("Análisis 1.xlsx")
        save_path = tmp_path / "other.xlsx"

        mock_imap = AsyncMock()
        mock_imap._client_task = asyncio.Future()
        mock_imap._client_task.set_result(None)
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock(return_value=MagicMock(result="OK", lines=[]))
        mock_imap.select = AsyncMock(return_value=("OK", [b"1"]))
        mock_imap.logout = AsyncMock()

        fetch_data = [b"1 FETCH (BODY[] {%d}" % len(raw_email), bytearray(raw_email), b")"]
        with patch.object(email_client, "_fetch_email_with_formats", return_value=fetch_data):
            with patch.object(email_client, "imap_class", return_value=mock_imap):
                with pytest.raises(ValueError, match="not found"):
                    await email_client.download_attachment(
                        email_id="1",
                        attachment_name="Different.xlsx",
                        save_path=str(save_path),
                    )
