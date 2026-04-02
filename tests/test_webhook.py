"""
Тесты вебхука: фильтрация типов сообщений, определение автора, кулдаун звонков.
"""
import sys
import os
import time
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.main import extract_message, ALLOWED_MSG_TYPES, MISSING_CALL_COOLDOWN


# ── extract_message ──────────────────────────────────────

def test_text_message():
    """Текстовое сообщение от клиента — status=inbound."""
    data = {
        "messageId": "msg-1",
        "chatId": "77012345678",
        "type": "text",
        "text": "Привет",
        "status": "inbound",
        "isEcho": False,
    }
    phone, text, file_url, msg_id, author, msg_type, status, is_echo = extract_message(data)
    assert phone == "77012345678"
    assert text == "Привет"
    assert file_url is None
    assert author == "client"
    assert msg_type == "text"
    assert status == "inbound"
    assert is_echo is False
    print("✅ test_text_message OK")


def test_bot_echo_message():
    """Исходящее от бота (isEcho=true, без sentFromApp) → author='bot'."""
    data = {
        "messageId": "msg-2",
        "chatId": "77012345678",
        "type": "text",
        "text": "Привет! Я Айдос",
        "status": "sent",
        "isEcho": True,
        "sentFromApp": False,
    }
    phone, text, file_url, msg_id, author, msg_type, status, is_echo = extract_message(data)
    assert author == "bot"
    assert is_echo is True
    print("✅ test_bot_echo_message OK")


def test_cashier_message():
    """Кассир пишет с телефона (isEcho=true, sentFromApp=true) → author='manager'."""
    data = {
        "messageId": "msg-3",
        "chatId": "77012345678",
        "type": "text",
        "text": "Заказ будет готов через 5 минут",
        "status": "delivered",
        "isEcho": True,
        "sentFromApp": True,
        "authorName": "Phone",
    }
    phone, text, file_url, msg_id, author, msg_type, status, is_echo = extract_message(data)
    assert author == "manager"
    assert is_echo is True
    print("✅ test_cashier_message OK")


def test_audio_message_has_file_url():
    """Аудио сообщение — file_url должен быть установлен."""
    data = {
        "messageId": "msg-4",
        "chatId": "77012345678",
        "type": "audio",
        "contentUri": "https://store.wazzup24.com/audio123.ogg",
        "status": "inbound",
        "isEcho": False,
    }
    phone, text, file_url, msg_id, author, msg_type, status, is_echo = extract_message(data)
    assert file_url == "https://store.wazzup24.com/audio123.ogg"
    assert msg_type == "audio"
    assert author == "client"
    print("✅ test_audio_message_has_file_url OK")


def test_document_pdf_has_file_url():
    """PDF документ — file_url должен быть."""
    data = {
        "messageId": "msg-5",
        "chatId": "77012345678",
        "type": "document",
        "contentUri": "https://store.wazzup24.com/receipt.pdf",
        "status": "inbound",
        "isEcho": False,
    }
    phone, text, file_url, msg_id, author, msg_type, status, is_echo = extract_message(data)
    assert file_url == "https://store.wazzup24.com/receipt.pdf"
    assert msg_type == "document"
    print("✅ test_document_pdf_has_file_url OK")


def test_image_no_file_url():
    """Изображение — file_url НЕ должен быть (не принимаем image как файл)."""
    data = {
        "messageId": "msg-6",
        "chatId": "77012345678",
        "type": "image",
        "contentUri": "https://store.wazzup24.com/photo.jpg",
        "status": "inbound",
        "isEcho": False,
    }
    phone, text, file_url, msg_id, author, msg_type, status, is_echo = extract_message(data)
    assert file_url is None  # image не попадает в file_url
    assert msg_type == "image"
    print("✅ test_image_no_file_url OK")


def test_sticker_not_in_allowed():
    """Стикер не входит в ALLOWED_MSG_TYPES."""
    assert "sticker" not in ALLOWED_MSG_TYPES
    assert "image" not in ALLOWED_MSG_TYPES
    assert "video" not in ALLOWED_MSG_TYPES
    assert "geo" not in ALLOWED_MSG_TYPES
    assert "vcard" not in ALLOWED_MSG_TYPES
    print("✅ test_sticker_not_in_allowed OK")


def test_allowed_types():
    """Допустимые типы: text, audio, document, missing_call."""
    assert "text" in ALLOWED_MSG_TYPES
    assert "audio" in ALLOWED_MSG_TYPES
    assert "document" in ALLOWED_MSG_TYPES
    assert "missing_call" in ALLOWED_MSG_TYPES
    print("✅ test_allowed_types OK")


def test_missing_call():
    """missing_call — распознаётся корректно."""
    data = {
        "messageId": "msg-7",
        "chatId": "77012345678",
        "type": "missing_call",
        "status": "inbound",
        "isEcho": False,
    }
    phone, text, file_url, msg_id, author, msg_type, status, is_echo = extract_message(data)
    assert msg_type == "missing_call"
    assert author == "client"
    print("✅ test_missing_call OK")


def test_video_ignored():
    """Video — file_url пустой, тип не в ALLOWED."""
    data = {
        "messageId": "msg-8",
        "chatId": "77012345678",
        "type": "video",
        "contentUri": "https://store.wazzup24.com/video.mp4",
        "status": "inbound",
        "isEcho": False,
    }
    phone, text, file_url, msg_id, author, msg_type, status, is_echo = extract_message(data)
    assert file_url is None
    assert msg_type == "video"
    assert msg_type not in ALLOWED_MSG_TYPES
    print("✅ test_video_ignored OK")


def test_delivered_status_bot():
    """status=delivered, isEcho=true, sentFromApp=false → бот."""
    data = {
        "messageId": "msg-9",
        "chatId": "77012345678",
        "type": "text",
        "text": "test",
        "status": "delivered",
        "isEcho": True,
        "sentFromApp": False,
    }
    _, _, _, _, author, _, _, is_echo = extract_message(data)
    assert author == "bot"
    print("✅ test_delivered_status_bot OK")


def test_read_status_cashier():
    """status=read, isEcho=true, sentFromApp=true → кассир."""
    data = {
        "messageId": "msg-10",
        "chatId": "77012345678",
        "type": "text",
        "text": "test",
        "status": "read",
        "isEcho": True,
        "sentFromApp": True,
        "authorName": "Phone",
    }
    _, _, _, _, author, _, _, _ = extract_message(data)
    assert author == "manager"
    print("✅ test_read_status_cashier OK")


def test_cashier_from_wazzup_phone():
    """Реальный кейс: кассир отправил фото чека из Wazzup Phone."""
    data = {
        "messageId": "a8864042-868d-454b-92c8-f576291002f8",
        "chatId": "77472337906",
        "chatType": "whatsapp",
        "type": "image",
        "isEcho": True,
        "sentFromApp": True,
        "authorName": "Phone",
        "status": "delivered",
        "contentUri": "https://store.wazzup24.com/some_image",
    }
    phone, text, file_url, msg_id, author, msg_type, status, is_echo = extract_message(data)
    assert author == "manager"
    assert is_echo is True
    assert msg_type == "image"
    print("✅ test_cashier_from_wazzup_phone OK")


# ── RUN ALL ──────────────────────────────────────────────

if __name__ == "__main__":
    test_text_message()
    test_bot_echo_message()
    test_cashier_message()
    test_audio_message_has_file_url()
    test_document_pdf_has_file_url()
    test_image_no_file_url()
    test_sticker_not_in_allowed()
    test_allowed_types()
    test_missing_call()
    test_video_ignored()
    test_delivered_status_bot()
    test_read_status_cashier()
    test_cashier_from_wazzup_phone()
    print(f"\n🎉 Все 13 тестов прошли!")
