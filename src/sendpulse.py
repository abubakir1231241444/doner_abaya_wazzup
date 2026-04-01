"""
SendPulse API — отправка сообщений клиентам WhatsApp.
"""
import logging
import httpx
from src.config import SENDPULSE_CLIENT_ID, SENDPULSE_CLIENT_SECRET, SENDPULSE_BOT_ID

logger = logging.getLogger(__name__)

_token: dict = {"access_token": None}

SENT_MESSAGES_CACHE: set[str] = set()

BASE_URL = "https://api.sendpulse.com"


async def _get_token() -> str:
    """Получить OAuth2 токен SendPulse."""
    if _token["access_token"]:
        return _token["access_token"]
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE_URL}/oauth/access_token", json={
            "grant_type":    "client_credentials",
            "client_id":     SENDPULSE_CLIENT_ID,
            "client_secret": SENDPULSE_CLIENT_SECRET,
        })
        data = r.json()
        _token["access_token"] = data.get("access_token")
        logger.info("SendPulse token refreshed")
        return _token["access_token"]


def _invalidate_token():
    _token["access_token"] = None


async def send_message(phone: str, text: str):
    """Отправить текстовое сообщение клиенту в WhatsApp."""
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "bot_id": SENDPULSE_BOT_ID,
        "phone": phone,
        "message": {"type": "text", "text": {"body": text}},
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/whatsapp/contacts/sendByPhone",
            json=payload, headers=headers, timeout=15
        )
        if r.status_code == 401:
            _invalidate_token()
            logger.warning("SendPulse 401 — token invalidated, retry next call")
        elif r.status_code not in (200, 201):
            logger.error(f"SendPulse send error {r.status_code}: {r.text}")
        else:
            logger.info(f"SendPulse → {phone}: sent {len(text)} chars")
            # Кешируем часть отправленного сообщения чтобы не поймать эхо-вебхук
            SENT_MESSAGES_CACHE.add(text[:100].strip())
            if len(SENT_MESSAGES_CACHE) > 1000:
                # Очищаем кэш если он слишком большой (по простому варианту - весь)
                SENT_MESSAGES_CACHE.clear()


async def send_welcome_buttons(phone: str, text: str):
    """Отправить сообщение с кнопками (Меню и Адрес) для нового пользователя."""
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "bot_id": SENDPULSE_BOT_ID,
        "phone": phone,
        "message": {
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": text},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": "btn_menu", "title": "🌯 Меню"}
                        },
                        {
                            "type": "reply",
                            "reply": {"id": "btn_address", "title": "📍 Наш адрес"}
                        }
                    ]
                }
            }
        }
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/whatsapp/contacts/sendByPhone",
            json=payload, headers=headers, timeout=15
        )
        if r.status_code not in (200, 201):
            logger.error(f"SendPulse buttons error {r.status_code}: {r.text}")
        else:
            logger.info(f"SendPulse → {phone}: sent welcome buttons")


async def send_image(phone: str, url: str, caption: str = None):
    """Отправить изображение клиенту в WhatsApp по прямой ссылке."""
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    img_data = {"link": url}
    if caption:
        img_data["caption"] = caption

    payload = {
        "bot_id": SENDPULSE_BOT_ID,
        "phone": phone,
        "message": {"type": "image", "image": img_data},
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/whatsapp/contacts/sendByPhone",
            json=payload, headers=headers, timeout=15
        )
        if r.status_code not in (200, 201):
            logger.error(f"SendPulse image error {r.status_code}: {r.text}")
        else:
            logger.info(f"SendPulse image → {phone}: sent")


async def send_typing(contact_id: str):
    """Показать индикатор набора текста."""
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{BASE_URL}/whatsapp/chats/typing",
                json={"contact_id": contact_id},
                headers=headers, timeout=5
            )
    except Exception as e:
        logger.warning(f"send_typing error: {e}")


async def set_automation(phone: str, automate: bool, contact_id: str = None):
    """Включить/выключить автоматизацию (встроенного бота) для контакта."""
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    
    async with httpx.AsyncClient() as client:
        if not contact_id:
            # Сначала найдем contact_id по телефону
            r_find = await client.get(
                f"{BASE_URL}/whatsapp/contacts/getByPhone?phone={phone}",
                headers=headers
            )
            data = r_find.json()
            contact_id = data.get("data", {}).get("id")
            if not contact_id:
                logger.warning(f"Contact {phone} not found for set_automation")
                return

        payload = {
            "contact_id": contact_id,
            "automate":   automate
        }
        r = await client.post(
            f"{BASE_URL}/whatsapp/chats/set-automation-status",
            json=payload, headers=headers, timeout=10
        )
        if r.status_code not in (200, 201):
            logger.error(f"SendPulse automation error {r.status_code}: {r.text}")
        else:
            status = "RESUMED" if automate else "PAUSED"
            logger.info(f"SendPulse automation for {phone}: {status}")


async def pause_automation(contact_id: str, minutes: int = 60):
    """Приостановить автоматизацию бота на заданное количество минут."""
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "contact_id": contact_id,
        "minutes": minutes
    }
    
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/whatsapp/chats/pauseAutomation",
            json=payload, headers=headers, timeout=10
        )
        if r.status_code not in (200, 201):
            logger.error(f"SendPulse pauseAutomation error {r.status_code}: {r.text}")
        else:
            logger.info(f"SendPulse automation paused for {minutes} min for contact_id: {contact_id}")


async def download_file(url: str, max_size: int = 5 * 1024 * 1024) -> bytes:
    """Скачать файл (PDF-чек) по URL из SendPulse (с лимитом размера 5 МБ потоком)."""
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    chunks = []
    downloaded = 0
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url, headers=headers, timeout=30, follow_redirects=True) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if downloaded > max_size:
                        logger.error(f"download_file error: File exceeds maximum allowed size of 5MB")
                        return b""
        return b"".join(chunks)
    except Exception as e:
        logger.error(f"download_file exception: {e}")
        return b""
