import pytest

from mcp_email_server.utils.html_converter import html_to_markdown


def test_basic_html_conversion():
    html = "<h1>Title</h1><p>Hello <strong>world</strong></p>"
    result = html_to_markdown(html)
    assert "# Title" in result
    assert "**world**" in result


def test_removes_tracking_pixels():
    html = '<p>Content</p><img width="1" height="1" src="https://tracker.example.com/pixel.gif">'
    result = html_to_markdown(html)
    assert "tracker.example.com" not in result
    assert "Content" in result


def test_preserves_links():
    html = '<p>Click <a href="https://example.com">here</a> for more.</p>'
    result = html_to_markdown(html)
    assert "[here](https://example.com)" in result


def test_handles_plain_text_input():
    text = "This is just plain text with no HTML tags."
    result = html_to_markdown(text)
    assert result == text


def test_empty_input():
    assert html_to_markdown("") == ""
    assert html_to_markdown(None) == ""
    assert html_to_markdown("   ") == ""


def test_removes_style_blocks():
    html = "<style>body { color: red; }</style><p>Visible content</p>"
    result = html_to_markdown(html)
    assert "color: red" not in result
    assert "Visible content" in result


def test_removes_script_tags():
    html = "<script>alert('xss')</script><p>Safe content</p>"
    result = html_to_markdown(html)
    assert "alert" not in result
    assert "Safe content" in result


def test_converts_bold_and_italic():
    html = "<b>bold</b> and <em>italic</em>"
    result = html_to_markdown(html)
    assert "**bold**" in result
    assert "*italic*" in result


def test_converts_images():
    html = '<img src="https://example.com/img.png" alt="My image">'
    result = html_to_markdown(html)
    assert "![My image](https://example.com/img.png)" in result


def test_converts_headers():
    html = "<h1>H1</h1><h2>H2</h2><h3>H3</h3>"
    result = html_to_markdown(html)
    assert "# H1" in result
    assert "## H2" in result
    assert "### H3" in result


def test_removes_hidden_elements():
    html = '<div style="display: none">Hidden</div><p>Visible</p>'
    result = html_to_markdown(html)
    assert "Hidden" not in result
    assert "Visible" in result


def test_cleans_special_characters():
    html = "<p>Non\u00a0breaking\u200bspace\u2013dash</p>"
    result = html_to_markdown(html)
    assert "\u00a0" not in result
    assert "\u200b" not in result


class TestHtmlToText:
    """Plain-text fallback for HTML-only mail (adapted from upstream #169)."""

    def test_title_and_styles_do_not_leak(self):
        from mcp_email_server.utils.html_converter import html_to_text

        html = "<html><head><title>Newsletter</title><style>p{color:red}</style></head><body><p>Hi</p></body></html>"
        assert html_to_text(html) == "Hi"

    def test_entities_decoded(self):
        from mcp_email_server.utils.html_converter import html_to_text

        assert (
            html_to_text("<p>Don&#8217;t &copy; 2026 &AMP; caf&eacute;&nbsp;now</p>") == "Don\u2019t © 2026 & café now"
        )

    def test_blocks_and_cells_are_separated(self):
        from mcp_email_server.utils.html_converter import html_to_text

        html = "<p>One</p><p>Two</p><table><tr><td>A</td><td>B</td></tr><tr><td>C</td></tr></table>Line<br>Break"
        assert html_to_text(html) == "One\n\nTwo\n\nA B\n\nC\n\nLine\nBreak"

    def test_unclosed_head_does_not_hide_body(self):
        from mcp_email_server.utils.html_converter import html_to_text

        assert html_to_text("<html><head><title>x<body><p>Visible</p>") == "Visible"

    def test_empty(self):
        from mcp_email_server.utils.html_converter import html_to_text

        assert html_to_text("") == ""
        assert html_to_text("   ") == ""


def test_parse_email_data_uses_html_fallback():
    from email.message import EmailMessage
    from unittest.mock import MagicMock

    from mcp_email_server.emails.classic import EmailClient

    msg = EmailMessage()
    msg["Subject"] = "HTML only"
    msg["From"] = "a@example.com"
    msg.set_content("<html><head><title>T</title></head><body><p>Hello &amp; welcome</p></body></html>", subtype="html")
    assert EmailClient(MagicMock())._parse_email_data(msg.as_bytes())["body"] == "Hello & welcome"


def test_invisible_preheader_padding_is_removed_but_emoji_joiners_kept():
    from mcp_email_server.utils.html_converter import html_to_text

    padding = "͏ ­ ​ ﻿ " * 50
    family = "\U0001f468‍\U0001f469‍\U0001f467"
    assert (
        html_to_text(f"<p>Sale ends tonight. {padding}</p><p>Hi {family}</p>") == f"Sale ends tonight.\n\nHi {family}"
    )


class TestHtmlToTextLinks:
    """Link URLs are kept in full, as footnotes by default (adapted from upstream #186)."""

    URL = "https://example.com/verify?token=" + "a" * 300
    HTML = (
        f'<p>Please <a href="{URL}">verify your email</a>.</p><a href="https://shop.example/sale"><img src="b.png"></a>'
    )

    def test_footnotes_default_puts_content_first(self):
        from mcp_email_server.utils.html_converter import html_to_text

        assert html_to_text(self.HTML) == (
            f"Please verify your email [1].\n\nLinks:\n[1] {self.URL}\n[2] https://shop.example/sale"
        )

    def test_footnotes_reuse_number_for_repeated_url(self):
        from mcp_email_server.utils.html_converter import html_to_text

        html = '<a href="https://x.example/a">One</a> <a href="https://x.example/b">Two</a> <a href="https://x.example/a">Again</a>'
        assert html_to_text(html) == (
            "One [1] Two [2] Again [1]\n\nLinks:\n[1] https://x.example/a\n[2] https://x.example/b"
        )

    def test_inline(self):
        from mcp_email_server.utils.html_converter import html_to_text

        assert html_to_text(self.HTML, links="inline") == (
            f"Please verify your email ({self.URL}).\nhttps://shop.example/sale"
        )

    def test_none(self):
        from mcp_email_server.utils.html_converter import html_to_text

        assert html_to_text(self.HTML, links="none") == "Please verify your email."

    def test_invalid_mode(self):
        from mcp_email_server.utils.html_converter import html_to_text

        with pytest.raises(ValueError, match="links must be one of"):
            html_to_text("<p>x</p>", links="all")

    def test_entities_in_href_decoded(self):
        from mcp_email_server.utils.html_converter import html_to_text

        assert html_to_text('<a href="https://x.example/?a=1&amp;b=2">Go</a>', links="inline") == (
            "Go (https://x.example/?a=1&b=2)"
        )

    @pytest.mark.parametrize("links", ["footnotes", "inline"])
    def test_text_that_is_the_url_not_repeated(self, links):
        from mcp_email_server.utils.html_converter import html_to_text

        assert html_to_text('<a href="https://example.com/">https://example.com</a>', links=links) == (
            "https://example.com"
        )

    @pytest.mark.parametrize(
        "href", ["#top", "mailto:help@example.com", "javascript:void(0)", " JavaScript:alert(1)", "java\tscript:x", ""]
    )
    def test_unhelpful_links_show_text_only(self, href):
        from mcp_email_server.utils.html_converter import html_to_text

        assert html_to_text(f'<a href="{href}">Contact us</a>') == "Contact us"
