"""
HTML Email to Markdown Converter
Converts complex HTML marketing emails to clean markdown format
"""

import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote


def html_to_markdown(html_content: str) -> str:
    """Convert HTML email content to markdown format"""

    if not html_content or not html_content.strip():
        return ""

    # Remove all CSS style blocks
    html_content = re.sub(r"<style[^>]*>.*?</style>", "", html_content, flags=re.DOTALL | re.IGNORECASE)

    # Remove HTML comments
    html_content = re.sub(r"<!--.*?-->", "", html_content, flags=re.DOTALL)

    # Remove script tags
    html_content = re.sub(r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE)

    # Remove tracking pixels and hidden elements
    html_content = re.sub(r'<img[^>]*width="1"[^>]*height="1"[^>]*>', "", html_content, flags=re.IGNORECASE)
    html_content = re.sub(r"<div[^>]*display:\s*none[^>]*>.*?</div>", "", html_content, flags=re.DOTALL | re.IGNORECASE)

    # Convert headers (h1-h6)
    for i in range(1, 7):
        html_content = re.sub(
            f"<h{i}[^>]*>(.*?)</h{i}>", f"{'#' * i} \\1\n", html_content, flags=re.DOTALL | re.IGNORECASE
        )

    # Convert paragraphs
    html_content = re.sub(r"<p[^>]*>(.*?)</p>", "\\1\n\n", html_content, flags=re.DOTALL | re.IGNORECASE)

    # Convert line breaks
    html_content = re.sub(r"<br\s*/?>", "\n", html_content, flags=re.IGNORECASE)

    # Convert links - extract clean URLs and create markdown links
    def clean_link(match):
        href = match.group(1)
        text = match.group(2)

        # Extract actual URL from tracking redirects
        if "target=" in href:
            target_match = re.search(r'target=([^&"]*)', href)
            if target_match:
                href = unquote(target_match.group(1))

        # Clean up text
        text = re.sub(r"<[^>]+>", "", text)  # Remove any HTML tags in text
        text = text.strip()

        if text and href:
            return f"[{text}]({href})"
        elif href:
            return f"<{href}>"
        else:
            return text

    html_content = re.sub(
        r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', clean_link, html_content, flags=re.DOTALL | re.IGNORECASE
    )

    # Convert images
    def convert_image(match):
        src = match.group(1)
        alt = match.group(2) if match.group(2) else "Image"
        return f"![{alt}]({src})"

    html_content = re.sub(
        r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*>', convert_image, html_content, flags=re.IGNORECASE
    )
    html_content = re.sub(r'<img[^>]*src="([^"]*)"[^>]*>', "![Image](\\1)", html_content, flags=re.IGNORECASE)

    # Convert strong/bold
    html_content = re.sub(
        r"<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>", "**\\1**", html_content, flags=re.DOTALL | re.IGNORECASE
    )

    # Convert emphasis/italic
    html_content = re.sub(r"<(?:em|i)[^>]*>(.*?)</(?:em|i)>", "*\\1*", html_content, flags=re.DOTALL | re.IGNORECASE)

    # Remove all remaining HTML tags
    html_content = re.sub(r"<[^>]+>", "", html_content)

    # Clean up whitespace
    html_content = re.sub(r"\n\s*\n\s*\n", "\n\n", html_content)  # Multiple newlines to double
    html_content = re.sub(r"[ \t]+", " ", html_content)  # Multiple spaces to single
    html_content = re.sub(r"^ +| +$", "", html_content, flags=re.MULTILINE)  # Trim line whitespace

    # Unescape HTML entities
    html_content = unescape(html_content)

    # Clean up special characters
    html_content = html_content.replace("\u00a0", " ")  # Non-breaking space
    html_content = html_content.replace("\u200b", "")  # Zero-width space
    html_content = html_content.replace("\u2014", "---")  # Em dash
    html_content = html_content.replace("\u2013", "-")  # En dash to hyphen

    return html_content.strip()


_TEXT_SKIP_TAGS = {"head", "title", "script", "style", "noscript", "template"}
_TEXT_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "center", "dd", "div", "dl", "dt", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "tfoot", "thead", "tr", "ul",
}  # fmt: skip
_TEXT_CELL_TAGS = {"td", "th"}


class _HtmlTextExtractor(HTMLParser):
    """Collect the readable text of an HTML document, with line breaks at block boundaries."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            # A <head> that was never closed must not hide the body.
            self.skip_depth = 0
        elif tag in _TEXT_SKIP_TAGS:
            self.skip_depth += 1
        elif tag in _TEXT_BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in _TEXT_CELL_TAGS:
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag in _TEXT_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _TEXT_SKIP_TAGS:
            self.skip_depth = max(self.skip_depth - 1, 0)
        elif tag in _TEXT_BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in _TEXT_CELL_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


_ALSO_INVISIBLE = {"\u034f"}  # COMBINING GRAPHEME JOINER is Mn, not Cf, but renders as nothing


def _is_invisible(ch: str) -> bool:
    return unicodedata.category(ch) == "Cf" and ch != "\u200d"


def html_to_text(html_content: str) -> str:
    """Convert an HTML email body to plain text for reading (used when there is no text/plain part).

    Built on the standard library parser: head/title/script/style are dropped, every
    entity is decoded, block elements and table rows become line breaks, table cells are
    separated, and runs of whitespace are collapsed.
    """
    if not html_content or not html_content.strip():
        return ""
    extractor = _HtmlTextExtractor()
    extractor.feed(html_content)
    extractor.close()
    text = "".join(extractor.parts).replace("\xa0", " ")
    # Marketing mail pads its preview text with hundreds of invisible characters (soft
    # hyphens, zero-width spaces, combining grapheme joiners); drop them, but keep the
    # zero-width joiner that emoji sequences need.
    text = "".join(ch for ch in text if ch not in _ALSO_INVISIBLE and not _is_invisible(ch))
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
