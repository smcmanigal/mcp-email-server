"""Non-ASCII search values: CHARSET UTF-8, literals where allowed, clear errors (port of upstream #190, adapted)."""

from unittest.mock import AsyncMock

import pytest

from mcp_email_server.emails.classic import _uid_search

OK = ("OK", [b"1 2", b"SEARCH completed"])


def _imap(capabilities=(), response=OK):
    imap = AsyncMock()
    imap.protocol.capabilities = set(capabilities)
    imap.uid_search = AsyncMock(return_value=response)
    return imap


@pytest.mark.asyncio
async def test_ascii_search_unchanged():
    imap = _imap({"LITERAL+"})
    await _uid_search(imap, ["SUBJECT", '"invoice"'])
    imap.uid_search.assert_awaited_once_with("SUBJECT", '"invoice"', charset=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", ["LITERAL+", "LITERAL-"])
async def test_non_ascii_sent_as_literal_when_supported(cap):
    imap = _imap({cap})
    await _uid_search(imap, ["SINCE", "24-OCT-2026", "OR", "SUBJECT", '"café"', "SUBJECT", '"plain"'])
    imap.uid_search.assert_awaited_once_with(
        "SINCE", "24-OCT-2026", "OR", "SUBJECT", "{5+}\r\ncafé", "SUBJECT", '"plain"', charset="UTF-8"
    )


@pytest.mark.asyncio
async def test_escapes_are_removed_inside_literal():
    imap = _imap({"LITERAL+"})
    await _uid_search(imap, ["SUBJECT", '"say \\"é\\" \\\\ ok"'])
    value = 'say "é" \\ ok'
    assert imap.uid_search.call_args.args == ("SUBJECT", f"{{{len(value.encode())}+}}\r\n{value}")


@pytest.mark.asyncio
async def test_literal_minus_limit_falls_back_to_quoted():
    imap = _imap({"LITERAL-"})
    long_value = "é" * 2049  # 4098 bytes > 4096
    await _uid_search(imap, ["BODY", f'"{long_value}"'])
    assert imap.uid_search.call_args.args == ("BODY", f'"{long_value}"')


@pytest.mark.asyncio
async def test_without_literal_support_sends_quoted_with_charset():
    imap = _imap({"UTF8=ACCEPT"})
    await _uid_search(imap, ["SUBJECT", '"café"'])
    imap.uid_search.assert_awaited_once_with("SUBJECT", '"café"', charset="UTF-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        ("NO", [b"[BADCHARSET (US-ASCII)] The specified charset is not supported."]),
        ("BAD", [b"Could not parse command"]),
    ],
)
async def test_refused_non_ascii_search_raises(response):
    imap = _imap({"LITERAL+"}, response)
    with pytest.raises(ValueError, match="does not support searching for non-ASCII text"):
        await _uid_search(imap, ["SUBJECT", '"café"'])


@pytest.mark.asyncio
async def test_other_failures_are_returned_not_raised():
    imap = _imap({"LITERAL+"}, ("NO", [b"Mailbox busy"]))
    result, _lines = await _uid_search(imap, ["SUBJECT", '"café"'])
    assert result == "NO"
