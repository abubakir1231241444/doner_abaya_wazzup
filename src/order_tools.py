"""
Обработка вызовов функций от LLM:
  - create_order  → создать заказ в БД + уведомить кассира
  - escalate_to_admin → уведомить кассира о жалобе
"""
import json
import logging
import httpx
from src.config import CASHIER_BOT_TOKEN, CASHIER_TG_IDS

logger = logging.getLogger(__name__)

DELIVERY_LABELS = {
    "takeaway":       "С собой 🥡",
    "in_cafe":        "В кафе 🍽",
    "client_courier": "Свой курьер 🚗",
}


async def _send_telegram(text: str, reply_markup: dict = None, order_id: int = None, receipt_bytes: bytes = None):
    """Отправить сообщение или документ в Telegram всем кассирам."""
    first_resp = {}
    for chat_id in CASHIER_TG_IDS:
        if receipt_bytes:
            url = f"https://api.telegram.org/bot{CASHIER_BOT_TOKEN}/sendDocument"
            data = {
                "chat_id": chat_id,
                "caption": text,
                "parse_mode": "HTML",
            }
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)
                
            files = {
                "document": (f"receipt_{order_id or 'new'}.pdf", receipt_bytes, "application/pdf")
            }
            
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.post(url, data=data, files=files, timeout=15)
                    r_json = r.json()
                    if not first_resp: first_resp = r_json
                    if r.status_code != 200:
                        logger.error(f"TG notify document error: {r.text}")
            except Exception as e:
                logger.error(f"TG notify document exception: {e}")
        else:
            payload: dict = {
                "chat_id":    chat_id,
                "text":       text,
                "parse_mode": "HTML",
            }
            if reply_markup:
                payload["reply_markup"] = json.dumps(reply_markup)
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.post(
                        f"https://api.telegram.org/bot{CASHIER_BOT_TOKEN}/sendMessage",
                        json=payload, timeout=10
                    )
                    r_json = r.json()
                    if not first_resp: first_resp = r_json
                    if r.status_code != 200:
                        logger.error(f"TG notify error: {r.text}")
            except Exception as e:
                logger.error(f"TG notify exception: {e}")
    return first_resp




async def handle_create_order(phone: str, args: dict, receipt_bytes: bytes = None) -> int | None:
    """
    Создать заказ в БД и уведомить кассира.
    Возвращает order_id.
    """
    from src import db  # отложенный импорт чтобы избежать цикла

    try:
        user = db.get_or_create_user(phone)
        user_id = user["id"]
    except Exception as e:
        logger.error(f"Error creating user in DB: {e}")
        from src import wazzup as wz
        await wz.send_message(phone, "Техническая ошибка связи с базой данных. Пожалуйста, повторите через пару минут 🙁")
        return None, None

    positions = args.get("positions", [])
    total     = args.get("summa", 0)
    dtype     = args.get("type", "takeaway")
    wish      = args.get("food_wish", "нет")
    address   = args.get("address")
    cphone    = args.get("phone")

    try:
        order = db.create_order(
            user_id=user_id,
            items=positions,
            total=total,
            delivery_type=dtype,
            food_wish=wish,
            address=address,
            phone=cphone,
        )
    except Exception as e:
        logger.error(f"Error creating order in DB: {e}")
        from src import wazzup as wz
        await wz.send_message(phone, "Техническая ошибка создания заказа. Пожалуйста, повторите через пару минут 🙁")
        return None, None
    order_id = order["id"]
    daily_id = order.get("daily_number", order_id)
    logger.info(f"Order #{order_id} (Daily #{daily_id}) created for {phone}")

    # Форматируем сообщение кассиру
    items_text = ""
    for p in positions:
        name  = p.get("name", "?")
        qty   = p.get("qty", 1)
        price = p.get("price", 0)
        size  = p.get("size", "")
        onion = "с луком" if p.get("onion", True) else "без лука"
        wish_i = p.get("wish", "") or ""
        details = f"{size}; {onion}" + (f"; {wish_i}" if wish_i and wish_i != "нет" else "")
        items_text += f"  • {name} ×{qty} ({details}) — {price * qty} тг\n"

    dt_label = DELIVERY_LABELS.get(dtype, dtype)
    msg = (
        f"🆕 <b>Новый заказ #{daily_id}</b>\n\n"
        f"{items_text}\n"
        f"<b>Итого:</b> {total} тг\n"
        f"<b>Тип:</b> {dt_label}\n"
    )
    if wish and wish != "нет":
        msg += f"<b>Пожелания:</b> {wish}\n"

    msg += f"\n<b>Телефон клиента:</b> +{phone}"

    # Кнопки: принять / отклонить
    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Принять",  "callback_data": f"accept_{order_id}_{phone}"},
                {"text": "❌ Отклонить", "callback_data": f"reject_{order_id}_{phone}"},
            ],
            [
                {"text": "💬 WhatsApp", "url": f"https://wa.me/{phone}"}
            ]
        ]
    }
    resp = await _send_telegram(msg, markup, order_id, receipt_bytes=receipt_bytes)
    # Сохраним message_id для reply-bridge
    tg_msg_id = resp.get("result", {}).get("message_id")

    return order_id, tg_msg_id


async def handle_escalate(phone: str, args: dict):
    """Уведомить кассира о жалобе клиента."""
    reason = args.get("reason", "не указана")
    msg = (
        f"⚠️ <b>Жалоба клиента</b>\n\n"
        f"<b>Телефон:</b> +{phone}\n"
        f"<b>Причина:</b> {reason}"
    )
    await _send_telegram(msg)
