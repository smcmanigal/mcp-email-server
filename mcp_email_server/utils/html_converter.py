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


def _useful_href(href: str | None) -> str | None:
    """The link target worth showing, or None for in-page anchors, mailto: and javascript: links."""
    if not href:
        return None
    href = "".join(ch for ch in href.strip() if ch.isprintable())
    scheme = re.sub(r"\s", "", href).lower()
    if not href or href.startswith("#") or scheme.startswith(("mailto:", "javascript:")):
        return None
    return href


LINK_MODES = ("footnotes", "inline", "none")


class _LinkFootnotes:
    """Numbered link list for "footnotes" mode; a repeated URL keeps its first number."""

    def __init__(self):
        self.urls: list[str] = []
        self._numbers: dict[str, int] = {}

    def number(self, url: str) -> int:
        if url not in self._numbers:
            self.urls.append(url)
            self._numbers[url] = len(self.urls)
        return self._numbers[url]

    def render(self) -> str:
        if not self.urls:
            return ""
        return "\n\nLinks:\n" + "\n".join(f"[{n}] {url}" for n, url in enumerate(self.urls, 1))


class _HtmlTextExtractor(HTMLParser):
    """Collect the readable text of an HTML document, with line breaks at block boundaries."""

    def __init__(self, links: str = "footnotes", footnotes: _LinkFootnotes | None = None):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.links = links
        self.footnotes = footnotes or _LinkFootnotes()
        self.link: tuple[str, int] | None = None  # (href, index in parts where its text starts)

    def handle_starttag(self, tag, attrs):
        if tag == "a" and not self.skip_depth and self.links != "none":
            href = _useful_href(dict(attrs).get("href"))
            self.link = (href, len(self.parts)) if href else None
        elif tag == "body":
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
        if tag == "a" and self.link:
            href, start = self.link
            self.link = None
            text = " ".join("".join(self.parts[start:]).split())
            if text.rstrip("/") == href.rstrip("/"):
                pass  # the text already is the URL
            elif self.links == "inline":
                self.parts.append(f" ({href})" if text else f" {href} ")
            else:
                # Footnotes: a marker after the link text, the full URL in the list at the end.
                # An image-only link has no text to mark, so it is only listed.
                number = self.footnotes.number(href)
                if text:
                    self.parts.append(f" [{number}]")
        elif tag in _TEXT_SKIP_TAGS:
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


def html_to_text(html_content: str, links: str = "footnotes") -> str:
    """Convert an HTML email body to plain text for reading (used when there is no text/plain part).

    Built on the standard library parser: head/title/script/style are dropped, every
    entity is decoded, block elements and table rows become line breaks, table cells are
    separated, and runs of whitespace are collapsed.

    links controls link URLs, always kept in full:
    - "footnotes" (default): "text [n]" in place, numbered URLs in a "Links:" list at the
      end, so the content comes first and body truncation cuts URLs before text;
    - "inline": "text (URL)" in place;
    - "none": text only.
    In-page anchors, mailto: and javascript: links are never shown.
    """
    if links not in LINK_MODES:
        raise ValueError(f"links must be one of {', '.join(LINK_MODES)}, not {links!r}")
    if not html_content or not html_content.strip():
        return ""
    extractor = _HtmlTextExtractor(links)
    extractor.feed(html_content)
    extractor.close()
    text = "".join(extractor.parts).replace("\xa0", " ")
    # Marketing mail pads its preview text with hundreds of invisible characters (soft
    # hyphens, zero-width spaces, combining grapheme joiners); drop them, but keep the
    # zero-width joiner that emoji sequences need.
    text = "".join(ch for ch in text if ch not in _ALSO_INVISIBLE and not _is_invisible(ch))
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + extractor.footnotes.render()


LONG_URL_LENGTH = 100
_PLAIN_URL = re.compile(r"(?P<lead>[ \t]?)(?P<open>[\[<(]?)(?P<url>https?://[^\s<>\[\]()\"']+)(?P<close>[\]>)]?)")
_WRAPPERS = {"[": "]", "<": ">", "(": ")"}


def relocate_long_urls(text: str, links: str = "footnotes", min_length: int = LONG_URL_LENGTH) -> str:
    """Move long URLs out of a plain-text body, matching html_to_text's link modes.

    Marketing mail's text/plain part can be mostly tracking URLs (one Micro Center ad: 152
    URLs of 566-758 characters, about 95% of the text), which pushes the content past the
    body length limit. URLs of min_length or more are handled per links:
    - "footnotes" (default): replaced by "[n]", listed in full in a "Links:" list at the end;
    - "none": removed;
    - "inline": left as they are.
    Shorter URLs are readable and stay in place. A URL wrapped in [], <> or () loses the
    wrapper along with it.
    """
    if links not in LINK_MODES:
        raise ValueError(f"links must be one of {', '.join(LINK_MODES)}, not {links!r}")
    if links == "inline" or not text:
        return text
    footnotes = _LinkFootnotes()

    def replace(match: re.Match) -> str:
        url = match.group("url")
        if len(url) < min_length:
            return match.group(0)
        lead = match.group("lead")
        opener, closer = match.group("open"), match.group("close")
        wrapped = bool(opener) and _WRAPPERS[opener] == closer
        outside = "" if wrapped else opener
        trailing = "" if wrapped else closer
        if links == "none":
            return outside + trailing  # drop the space before the URL too
        return f"{lead}{outside}[{footnotes.number(url)}]{trailing}"

    relocated = _PLAIN_URL.sub(replace, text)
    if links == "none":
        relocated = re.sub(r"[ \t]+(\r?\n)", r"\1", relocated)
    return relocated.rstrip() + footnotes.render()
