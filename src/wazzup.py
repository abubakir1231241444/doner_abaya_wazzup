"""
Wazzup API v3 — отправка сообщений клиентам WhatsApp.
Замена SendPulse. Документация: https://wazzup24.com/api/v3
"""
import logging
import time
import hashlib
import httpx
from src.config import WAZZUP_API_KEY, WAZZUP_CHANNEL_ID

logger = logging.getLogger(__name__)

BASE_URL = "https://api.wazzup24.com/v3"

# Эхо-детекция: MD5-хеш текста → timestamp
SENT_MESSAGES_CACHE: dict[str, float] = {}
# Последняя отправка по номеру: phone → timestamp
SENT_TIMESTAMPS: dict[str, float] = {}


def _cache_sent(phone: str, text: str):
    """Запомнить отправленное сообщение для эхо-детекции."""
    h = hashlib.md5(text.strip().encode('utf-8')).hexdigest()
    now = time.time()
    SENT_MESSAGES_CACHE[h] = now
    SENT_TIMESTAMPS[phone] = now
    
    # Резервная запись в базу данных для кросс-процессного эхо-детекта (кассир -> вебхук)
    try:
        from src import db
        # Используем "eco_hash" как message_id, чтобы отследить это сообщение
        db.save_wazzup_message(f"eco_{h}", phone, text, is_outgoing=True)
    except Exception as e:
        logger.warning(f"Cross-process cache warning: {e}")

    # Чистим локальные записи старше 5 минут
    for k in list(SENT_MESSAGES_CACHE):
        if now - SENT_MESSAGES_CACHE[k] > 300:
            del SENT_MESSAGES_CACHE[k]


def is_echo(phone: str, text: str) -> bool:
    """Проверить, является ли сообщение эхом нашей отправки (в том числе из другого процесса)."""
    if not text:
        return False
        
    h = hashlib.md5(text.strip().encode('utf-8')).hexdigest()
    
    # 1. Сначала быстрая локальная проверка
    if h in SENT_MESSAGES_CACHE:
        SENT_MESSAGES_CACHE.pop(h, None)
        return True
        
    # 2. Глобальная проверка в БД (если сообщение отправил другой процесс, например cashier_bot)
    try:
        from src import db
        res = db.get_db().table("wazzup_messages").select("id").eq("message_id", f"eco_{h}").execute()
        if res.data:
            logger.info("Echo detected globally via database cross-check")
            # Удаляем, чтобы не срабатывало дважды, хотя upsert и так работает
            db.get_db().table("wazzup_messages").delete().eq("message_id", f"eco_{h}").execute()
            return True
    except Exception as e:
        logger.warning(f"Global echo check failed: {e}")

    # 3. Тайм-аут: крайне маловероятно, но если отправляли < 3 секунд назад
    last_sent = SENT_TIMESTAMPS.get(phone, 0)
    if time.time() - last_sent < 3.0:
        return True
        
    return False


def _headers() -> dict:
    """Заголовки авторизации для всех запросов."""
    return {
        "Authorization": f"Bearer {WAZZUP_API_KEY}",
        "Content-Type": "application/json",
    }


async def send_message(phone: str, text: str) -> bool:
    """
    Отправить текстовое сообщение клиенту в WhatsApp через Wazzup.
    phone: номер без +, например 77012345678
    """
    # chatId для WhatsApp — только цифры (международный формат без +)
    chat_id = phone.lstrip("+")

    payload = {
        "channelId": WAZZUP_CHANNEL_ID,
        "chatType":  "whatsapp",
        "chatId":    chat_id,
        "text":      text,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{BASE_URL}/message",
                json=payload,
                headers=_headers(),
            )
            if r.status_code in (200, 201):
                logger.info(f"Wazzup → {phone}: sent {len(text)} chars")
                _cache_sent(chat_id, text)
                return True
            else:
                logger.error(f"Wazzup send error {r.status_code}: {r.text}")
                return False
    except Exception as e:
        logger.error(f"Wazzup send exception: {e}")
        return False


async def send_image(phone: str, url: str, caption: str = None) -> bool:
    """
    Отправить изображение клиенту через Wazzup (по прямой URL).
    Если есть caption — сначала отправляем файл, потом текст отдельным сообщением
    (Wazzup не поддерживает caption к файлу напрямую в базовом API).
    """
    chat_id = phone.lstrip("+")

    payload = {
        "channelId":  WAZZUP_CHANNEL_ID,
        "chatType":   "whatsapp",
        "chatId":     chat_id,
        "contentUri": url,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{BASE_URL}/message",
                json=payload,
                headers=_headers(),
            )
            if r.status_code in (200, 201):
                logger.info(f"Wazzup image → {phone}: sent")
                # Если есть подпись — шлём отдельным сообщением
                if caption:
                    await send_message(phone, caption)
                return True
            else:
                logger.error(f"Wazzup image error {r.status_code}: {r.text}")
                return False
    except Exception as e:
        logger.error(f"Wazzup image exception: {e}")
        return False


async def send_welcome_buttons(phone: str, text: str) -> bool:
    """
    Wazzup не поддерживает кнопки WhatsApp напрямую через базовый API.
    Отправляем обычный текст — это нормально.
    """
    return await send_message(phone, text)


async def download_file(url: str, max_size: int = 5 * 1024 * 1024) -> bytes:
    """
    Скачать файл (PDF-чек) по URL из Wazzup.
    Вебхук Wazzup содержит contentUri — прямая ссылка на файл.
    Авторизация нужна через Bearer токен.
    """
    chunks = []
    downloaded = 0
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "GET", url,
                headers=_headers(),
                follow_redirects=True
            ) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if downloaded > max_size:
                        logger.error("download_file: превышен лимит 5MB")
                        return b""
        return b"".join(chunks)
    except Exception as e:
        logger.error(f"download_file exception: {e}")
        return b""


async def register_webhook(webhook_url: str) -> bool:
    """
    Зарегистрировать/обновить URL вебхука в Wazzup.
    Wazzup отправит POST { "test": true } для верификации — сервер должен ответить 200.

    Вызывайте этот метод после запуска сервера и создания туннеля.
    """
    payload = {
        "webhooksUri": webhook_url,
        "subscriptions": {
            "messagesAndStatuses": True,
            "contactsAndDealsCreation": False,
            "channelsUpdates": False,
            "templateStatus": False,
        }
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.patch(
                f"{BASE_URL}/webhooks",
                json=payload,
                headers=_headers(),
            )
            if r.status_code in (200, 204):
                logger.info(f"Wazzup webhook registered: {webhook_url}")
                return True
            else:
                logger.error(f"Wazzup webhook registration error {r.status_code}: {r.text}")
                return False
    except Exception as e:
        logger.error(f"Wazzup register_webhook exception: {e}")
        return False


async def get_webhook_info() -> dict:
    """Получить текущий URL вебхука из Wazzup."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BASE_URL}/webhooks",
                headers=_headers(),
            )
            if r.status_code == 200:
                return r.json()
            return {}
    except Exception as e:
        logger.error(f"Wazzup get_webhook_info exception: {e}")
        return {}
