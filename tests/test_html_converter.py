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
