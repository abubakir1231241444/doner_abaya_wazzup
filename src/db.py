"""
Supabase CRUD — все обращения к БД через этот модуль.
"""
import json
import logging
from typing import Optional
from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

_client: Optional[Client] = None

def get_db() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


# ── USERS ─────────────────────────────────────────────────

def get_or_create_user(phone: str) -> dict:
    db = get_db()
    res = db.table("users").select("*").eq("whatsapp_phone", phone).execute()
    if res.data:
        return res.data[0]
    new_user = db.table("users").insert({"whatsapp_phone": phone}).execute()
    return new_user.data[0]

def set_user_lang(phone: str, lang: str):
    get_db().table("users").update({"lang": lang}).eq("whatsapp_phone", phone).execute()

def set_user_paused(phone: str, paused: bool):
    get_db().table("users").update({"is_paused": paused}).eq("whatsapp_phone", phone).execute()

def is_user_paused(phone: str) -> bool:
    db = get_db()
    res = db.table("users").select("is_paused").eq("whatsapp_phone", phone).execute()
    return bool(res.data and res.data[0].get("is_paused"))


# ── MENU ──────────────────────────────────────────────────

def get_menu() -> list[dict]:
    """Возвращает все позиции меню, отсортированные по category и sort_order."""
    res = get_db().table("menu").select("*").order("category").order("sort_order").execute()
    return res.data or []

def set_item_availability(item_id: int, available: bool):
    get_db().table("menu").update({"is_available": available}).eq("id", item_id).execute()


# ── ORDERS ────────────────────────────────────────────────

def create_order(
    user_id: int,
    items: list,
    total: int,
    delivery_type: str,
    food_wish: str = "нет",
    address: str | None = None,
    phone: str | None = None,
) -> dict:
    from datetime import datetime, timezone
    import pytz
    from src.config import TZ
    
    db = get_db()
    
    # Расчет номера заказа на текущие сутки (местное время)
    tz = pytz.timezone(TZ)
    now_local = datetime.now(tz)
    start_of_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = start_of_day.astimezone(timezone.utc).isoformat()
    
    # Считаем сколько заказов уже было сегодня
    res_count = db.table("orders").select("id", count="exact").gte("created_at", start_of_day_utc).execute()
    daily_number = (res_count.count or 0) + 1

    payload = {
        "user_id":          user_id,
        "items_json":       items,
        "total_sum":        total,
        "delivery_type":    delivery_type,
        "food_wish":        food_wish,
        "delivery_address": address,
        "delivery_phone":   phone,
        "status":           "new",
        "daily_number":     daily_number,
    }
    res = db.table("orders").insert(payload).execute()
    return res.data[0]

def update_order_status(order_id: int, status: str):
    get_db().table("orders").update({"status": status}).eq("id", order_id).execute()

def get_order(order_id: int) -> Optional[dict]:
    res = get_db().table("orders").select("*").eq("id", order_id).execute()
    return res.data[0] if res.data else None


# ── CONVERSATIONS (history) ────────────────────────────────

def get_history(phone: str) -> list:
    db = get_db()
    res = db.table("conversations").select("messages, updated_at").eq("phone", phone).execute()
    if res.data:
        record = res.data[0]
        updated_at_str = record.get("updated_at")
        if updated_at_str:
            from datetime import datetime, timezone, timedelta
            try:
                # updated_at format is usually ISO with +00:00 or Z
                dt = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if now - dt > timedelta(hours=12):
                    logger.info(f"History for {phone} is older than 12h. Auto-resetting.")
                    reset_history(phone)
                    return []
            except Exception as e:
                logger.warning(f"Error parsing updated_at {updated_at_str}: {e}")
        return record.get("messages", [])
    return []

def save_history(phone: str, messages: list):
    db = get_db()
    res = db.table("conversations").select("id").eq("phone", phone).execute()
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    if res.data:
        db.table("conversations").update({
            "messages": messages,
            "updated_at": now_iso
        }).eq("phone", phone).execute()
    else:
        db.table("conversations").insert({
            "phone": phone,
            "messages": messages
        }).execute()

def reset_history(phone: str):
    get_db().table("conversations").delete().eq("phone", phone).execute()


# ── WAZZUP MESSAGES (для цитирования) ─────────────────────

def save_wazzup_message(message_id: str, chat_id: str, text: str, is_outgoing: bool = True):
    """Сохранить сообщение для резолва цитат (quoted messages)."""
    try:
        get_db().table("wazzup_messages").upsert({
            "message_id": message_id,
            "chat_id": chat_id,
            "text": text,
            "is_outgoing": is_outgoing,
        }).execute()
    except Exception as e:
        logger.warning(f"save_wazzup_message error: {e}")


def get_message_info(message_id: str) -> tuple[str | None, bool]:
    """Найти текст сообщения и флаг is_outgoing по его Wazzup messageId."""
    try:
        res = get_db().table("wazzup_messages").select("text, is_outgoing").eq("message_id", message_id).execute()
        if res.data:
            return res.data[0].get("text"), res.data[0].get("is_outgoing", False)
    except Exception as e:
        logger.warning(f"get_message_info error: {e}")
    return None, False
