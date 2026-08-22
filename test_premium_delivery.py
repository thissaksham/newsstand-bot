"""
Unit tests for premium PDF delivery helpers.
"""

import asyncio
import io
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from utils.helpers import is_url, pdf_buffer, download_url_to_bytes
from run_scrapers import _send_premium_pdf_bytes, _send_premium_pdf_file_id


def test_is_url():
    assert is_url("https://www.indiags.com/go/abc")
    assert is_url("http://example.com")
    assert not is_url("BQACAgUAAxkBAA")
    assert not is_url("")


def test_pdf_buffer():
    buf = pdf_buffer(b"PDF content")
    assert isinstance(buf, io.BytesIO)
    assert buf.read() == b"PDF content"


async def fake_download(url: str):
    return b"fake pdf bytes"


def test_send_premium_pdf_bytes():
    bot = AsyncMock()
    sent_msg = MagicMock()
    sent_msg.document.file_id = "telegram_file_id_123"
    bot.send_document = AsyncMock(return_value=sent_msg)

    result = asyncio.run(_send_premium_pdf_bytes(
        bot, 12345, "The Hindu", date(2026, 8, 22), b"pdf bytes"
    ))
    assert result == "telegram_file_id_123"
    bot.send_document.assert_called_once()
    call_kwargs = bot.send_document.call_args.kwargs
    assert call_kwargs["chat_id"] == 12345
    assert call_kwargs["filename"] == "The_Hindu_2026-08-22.pdf"


def test_send_premium_pdf_file_id():
    bot = AsyncMock()
    bot.send_document = AsyncMock(return_value=None)

    result = asyncio.run(_send_premium_pdf_file_id(
        bot, 12345, "The Hindu", date(2026, 8, 22), "telegram_file_id_123"
    ))
    assert result is True
    bot.send_document.assert_called_once()
    assert bot.send_document.call_args.kwargs["document"] == "telegram_file_id_123"


if __name__ == "__main__":
    test_is_url()
    test_pdf_buffer()
    test_send_premium_pdf_bytes()
    test_send_premium_pdf_file_id()
    print("test_premium_delivery: OK")
