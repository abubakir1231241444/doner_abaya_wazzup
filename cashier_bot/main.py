"""
Бот Кассира — Telegram Bot 1.
"""
import asyncio
import json
import logging

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import CASHIER_BOT_TOKEN, CASHIER_TG_IDS
from src import db, wazzup as wz
from src.menu_builder import get_stoplist_grouped

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

router = Router()

CACHE_FILE = os.path.join(os.path.dirname(__file__), "msg_map.json")

def load_msg_map() -> dict[int, str]:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            try:
                return {int(k): v for k, v in json.load(f).items()}
            except: pass
    return {}

def save_msg_map():
    with open(CACHE_FILE, "w") as f:
        json.dump({str(k): v for k, v in _order_msg_map.items()}, f)

# message_id → phone (для reply-bridge)
_order_msg_map: dict[int, str] = load_msg_map()

FASTAPI_BASE = "http://localhost:8001"

# Постоянная клавиатура внизу экрана
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Аналитика"), KeyboardButton(text="🛑 Стоп-лист")],
        [KeyboardButton(text="📝 Меню")],
    ],
    resize_keyboard=True,
    is_persistent=True
)


# ── /start ────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message):
    await msg.answer(
        "👋 <b>Бот Кассира «Донер на Абае»</b>\n\n"
        "Используйте кнопки внизу для быстрого доступа ⬇️",
        reply_markup=MAIN_KEYBOARD,
        parse_mode=ParseMode.HTML
    )


# ── Обработчики постоянной клавиатуры ────────────────

@router.message(F.text == "📊 Аналитика")
async def btn_analytics(msg: Message):
    kb = [
        [InlineKeyboardButton(text="📅 За сегодня", callback_data="stats_today")],
        [InlineKeyboardButton(text="📅 За вчера", callback_data="stats_yesterday")],
        [InlineKeyboardButton(text="📈 За неделю", callback_data="stats_week")],
        [InlineKeyboardButton(text="📈 За месяц", callback_data="stats_month")],
        [InlineKeyboardButton(text="🏆 Топ блюд", callback_data="stats_top")],
    ]
    await msg.answer("📊 <b>Аналитика</b> — выберите период:",
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
                     parse_mode=ParseMode.HTML)

@router.message(F.text == "🛑 Стоп-лист")
async def btn_stoplist(msg: Message):
    groups = get_stoplist_grouped()
    buttons = []
    for g in groups:
        if g["auto_blocked"]:
            icon = "🚫"  # авто-заблокировано
            suffix = " (авто)"
        elif g["is_available"]:
            icon = "✅"
            suffix = " В наличии"
        else:
            icon = "❌"
            suffix = " Не в наличии"
        # ids кодируем через тире
        ids_str = "-".join(str(i) for i in g["ids"])
        current = "1" if g["is_available"] else "0"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {g['label']}{suffix}",
            callback_data=f"tgl_{ids_str}_{current}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Закрыть", callback_data="close_msg")])
    await msg.answer("🛑 <b>Стоп-лист</b>\nНажми на позицию чтобы включить/выключить:\n"
                     "⚠️ <i>Ассорти автоматически отключается если нет курицы или говядины</i>",
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                     parse_mode=ParseMode.HTML)

@router.message(F.text == "📝 Меню")
async def btn_menu(msg: Message):
    items = db.get_menu()
    if not items:
        await msg.answer("Меню пустое.")
        return
    text = "🌯 <b>Меню:</b>\n\n"
    for item in items:
        icon = "✅" if item["is_available"] else "❌"
        text += f"{icon} {item['name']} — {item['price']} тг\n"
    await msg.answer(text, parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "close_msg")
async def close_msg(cb: CallbackQuery):
    await cb.message.delete()
    await cb.answer()


# ── АНАЛИТИКА (расширенная) ───────────────────────

@router.callback_query(F.data.startswith("stats_"))
async def cb_stats_period(cb: CallbackQuery):
    from datetime import datetime, date, timedelta
    period = cb.data.split("_")[1]
    db_inst = db.get_db()
    today = date.today()

    if period == "today":
        start = today.isoformat()
        label = f"Сегодня ({today})"
    elif period == "yesterday":
        start = (today - timedelta(days=1)).isoformat()
        label = f"Вчера ({start})"
    elif period == "week":
        start = (today - timedelta(days=7)).isoformat()
        label = f"За 7 дней ({start} — {today})"
    elif period == "month":
        start = (today - timedelta(days=30)).isoformat()
        label = f"За 30 дней ({start} — {today})"
    elif period == "top":
        # Топ блюд за всё время
        res = db_inst.table("orders").select("items_json").eq("status", "completed").execute()
        orders = res.data or []
        from collections import Counter
        counter = Counter()
        for o in orders:
            items = o.get("items_json") or []
            for it in items:
                counter[it.get("name", "?")] += it.get("qty", 1)
        if not counter:
            await cb.message.answer("Пока нет данных.")
            await cb.answer()
            return
        top = counter.most_common(10)
        text = "🏆 <b>Топ блюд (всё время):</b>\n\n"
        for i, (name, cnt) in enumerate(top, 1):
            text += f"{i}. {name} — {cnt} шт\n"
        await cb.message.answer(text, parse_mode=ParseMode.HTML)
        await cb.answer()
        return
    else:
        await cb.answer("Неизвестный период")
        return

    # Общий запрос для дней / недели / месяца
    if period == "yesterday":
        end = today.isoformat()
        res = db_inst.table("orders").select("total_sum, status, delivery_type").gte("created_at", start).lt("created_at", end).execute()
    else:
        res = db_inst.table("orders").select("total_sum, status, delivery_type").gte("created_at", start).execute()
    orders = res.data or []

    total_count = len(orders)
    completed = [o for o in orders if o["status"] == "completed"]
    revenue = sum(o.get("total_sum", 0) for o in completed)
    avg = round(revenue / len(completed)) if completed else 0
    deliveries = sum(1 for o in orders if o.get("delivery_type") == "our_delivery")
    takeaway = sum(1 for o in orders if o.get("delivery_type") == "takeaway")

    msg = (
        f"📊 <b>Аналитика: {label}</b>\n\n"
        f"📦 Всего заказов: {total_count}\n"
        f"✅ Выполнено: {len(completed)}\n"
        f"💰 Выручка: {revenue} тг\n"
        f"💳 Средний чек: {avg} тг\n\n"
        f"🛵 Доставка: {deliveries}  |  🏠 Самовывоз: {takeaway}"
    )
    await cb.message.answer(msg, parse_mode=ParseMode.HTML)
    await cb.answer()

# Старый обработчик для совместимости
@router.callback_query(F.data == "stats")
async def cb_stats_old(cb: CallbackQuery):
    cb.data = "stats_today"
    await cb_stats_period(cb)

@router.callback_query(F.data == "stoplist_menu")
async def cb_stoplist(cb: CallbackQuery):
    items = db.get_menu()
    buttons = []
    for item in items:
        status_icon = "✅" if item["is_available"] else "❌"
        buttons.append([InlineKeyboardButton(
            text=f"{status_icon} {item['name']}",
            callback_data=f"toggle_{item['id']}_{1 if item['is_available'] else 0}"
        )])
    await cb.message.answer("🛑 <b>Стоп-лист</b>:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)
    await cb.answer()

# ── STOPLIST ──────────────────────────────────────────────

@router.message(Command("stoplist"))
async def cmd_stoplist(msg: Message):
    items = db.get_menu()
    if not items:
        await msg.answer("Меню пустое.")
        return

    buttons = []
    for item in items:
        status_icon = "✅" if item["is_available"] else "❌"
        buttons.append([InlineKeyboardButton(
            text=f"{status_icon} {item['name']} ({item['price']} тг)",
            callback_data=f"toggle_{item['id']}_{1 if item['is_available'] else 0}"
        )])

    await msg.answer(
        "🛑 <b>Стоп-лист</b>\nНажми на позицию чтобы включить/выключить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("tgl_"))
async def toggle_group(cb: CallbackQuery):
    """tgl_1-2_1  → ids=[1,2], текущее состояние=1 (вкл) → выкл."""
    parts = cb.data.split("_")  # tgl, ids_str, current
    ids_str = parts[1]
    current = parts[2]
    new_state = current == "0"  # 0 сейчас выкл → включить

    ids = [int(x) for x in ids_str.split("-")]
    for item_id in ids:
        db.set_item_availability(item_id, new_state)

    icon = "✅" if new_state else "❌"
    await cb.answer(f"{icon} Обновлено!")

    # Перестроить клавиатуру
    groups = get_stoplist_grouped()
    buttons = []
    for g in groups:
        if g["auto_blocked"]:
            gi = "🚫"
            suffix = " (авто)"
        elif g["is_available"]:
            gi = "✅"
            suffix = " В наличии"
        else:
            gi = "❌"
            suffix = " Не в наличии"
        gids = "-".join(str(i) for i in g["ids"])
        cur = "1" if g["is_available"] else "0"
        buttons.append([InlineKeyboardButton(
            text=f"{gi} {g['label']}{suffix}",
            callback_data=f"tgl_{gids}_{cur}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Закрыть", callback_data="close_msg")])
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data == "main_menu")
async def back_to_main(cb: CallbackQuery):
    await cb.message.delete()
    await cb.answer()


# ── УТИЛИТА РЕДАКТИРОВАНИЯ ТЕКСТА/ЦЕПШНА СООБЩЕНИЯ ────────

async def edit_ord_msg(msg: Message, new_text: str, reply_markup=None):
    """Безопасное редактирование сообщения заказа (текст или документ с caption)."""
    try:
        if msg.document or msg.photo:
            await msg.edit_caption(
                caption=new_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            await msg.edit_text(
                text=new_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Error editing message: {e}")

def get_ord_text(msg: Message) -> str:
    html_txt = getattr(msg, "html_text", None)
    if html_txt: return html_txt
    return getattr(msg, "caption", getattr(msg, "text", "")) or ""


# ── ACCEPT / REJECT ORDER ─────────────────────────────────

@router.callback_query(F.data.startswith("accept_"))
async def accept_order(cb: CallbackQuery):
    parts = cb.data.split("_")  # accept_<order_id>_<phone>
    order_id = int(parts[1])
    phone    = parts[2]

    db.update_order_status(order_id, "preparing")

    order = db.get_order(order_id)
    dtype = order["delivery_type"] if order else "takeaway"

    # Уведомить клиента
    await wz.send_message(phone, "Ваш заказ принят в работу! Готовим 🌯")

    # Убираем старый текст с кнопками Accept/Reject и заменяем на новый статус
    # Извлекаем оригинальный текст заказа (до возможных прошлых статусов)
    original_text = get_ord_text(cb.message)
    # Убирам старые статусные строки если есть
    for tag in [
        "<b>✅ ПРИНЯТ В РАБОТУ</b>", "✅ ПРИНЯТ В РАБОТУ",
        "<b>❌ ОТКЛОНЁН</b>", "❌ ОТКЛОНЁН", 
        "<b>🛵 Ждём курьера...</b>", "🛵 Ждём курьера...", 
        "<b>✅ ВЫПОЛНЕН</b>", "✅ ВЫПОЛНЕН"
    ]:
        original_text = original_text.replace(tag, "")
    original_text = original_text.strip()

    new_text = original_text + "\n\n<b>✅ ПРИНЯТ В РАБОТУ</b>"

    # Формируем НОВЫЕ кнопки (полностью заменяем старые)
    buttons = [
        [InlineKeyboardButton(text="💬 Написать клиенту (WA)", url=f"https://wa.me/{phone}")],
        [InlineKeyboardButton(text="⏳ Задержка 10 мин", callback_data=f"delay_{order_id}_{phone}")],
    ]
    buttons.append([InlineKeyboardButton(text="🔔 Заказ готов", callback_data=f"ready_{order_id}_{phone}")])
    buttons.append([InlineKeyboardButton(text="✅ Завершить заказ", callback_data=f"complete_{order_id}_{phone}")])

    buttons.append([InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"reject_{order_id}_{phone}")])

    _order_msg_map[cb.message.message_id] = phone  # для reply-bridge
    save_msg_map()

    await edit_ord_msg(
        cb.message,
        new_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await cb.answer("✅ Заказ принят!")


@router.callback_query(F.data.startswith("delay_"))
async def delay_order(cb: CallbackQuery):
    parts = cb.data.split("_")
    phone = parts[2]
    msg = "Приносим извинения, ваш заказ задерживается примерно на 10 минут. Мы уже очень торопимся! 🥙"
    await wz.send_message(phone, msg)
    await cb.answer("✅ Клиент уведомлен о задержке!")


@router.callback_query(F.data.startswith("ready_"))
async def ready_order(cb: CallbackQuery):
    """'Заказ готов' — текст зависит от типа доставки."""
    parts = cb.data.split("_")
    order_id = int(parts[1])
    phone = parts[2]

    order = db.get_order(order_id)
    dtype = order["delivery_type"] if order else "takeaway"

    messages = {
        "takeaway":       "Ваш заказ готов! Ожидаем вас 🌯",
        "in_cafe":        "Ваш заказ готов! Приятного аппетита 🍽",
        "client_courier": "Ваш заказ готов! Передаём вашему курьеру 📦",
    }
    text = messages.get(dtype, "Ваш заказ готов! 🌯")
    await wz.send_message(phone, text)

    # Убираем кнопку "Заказ готов" (чтобы не нажимать второй раз)
    kb = cb.message.reply_markup
    if kb:
        new_keyboard = []
        for row in kb.inline_keyboard:
            # Оставляем только те кнопки, которые НЕ начинаются на ready_
            new_row = [btn for btn in row if not (btn.callback_data and str(btn.callback_data).startswith("ready_"))]
            if new_row:
                new_keyboard.append(new_row)
        try:
            from aiogram.types import InlineKeyboardMarkup
            await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=new_keyboard))
        except Exception as e:
            logger.error(f"Failed to remove ready button: {e}")

    await cb.answer("🔔 Клиент уведомлён, кнопка скрыта!")


@router.callback_query(F.data.startswith("reject_"))
async def reject_order(cb: CallbackQuery):
    parts = cb.data.split("_")
    order_id = int(parts[1])
    phone    = parts[2]

    db.update_order_status(order_id, "cancelled")

    # Снять паузу с клиента
    db.set_user_paused(phone, False)
    await wz.send_message(phone, "К сожалению, ваш заказ не удалось принять. Попробуйте оформить заново 🙂")

    original_text = get_ord_text(cb.message)
    for tag in [
        "<b>✅ ПРИНЯТ В РАБОТУ</b>", "✅ ПРИНЯТ В РАБОТУ",
        "<b>❌ ОТКЛОНЁН</b>", "❌ ОТКЛОНЁН", 
        "<b>🛵 Ждём курьера...</b>", "🛵 Ждём курьера...", 
        "<b>✅ ВЫПОЛНЕН</b>", "✅ ВЫПОЛНЕН"
    ]:
        original_text = original_text.replace(tag, "")
    original_text = original_text.strip()

    # Убираем ВСЕ кнопки (reply_markup=None)
    await edit_ord_msg(
        cb.message,
        original_text + "\n\n<b>❌ ОТКЛОНЁН</b>",
        reply_markup=None
    )
    await cb.answer("Заказ отклонён.")


# ── REPLY-BRIDGE: кассир отвечает / шлет фото → пишем в WA ───

@router.message(F.reply_to_message)
async def reply_bridge(msg: Message, bot: Bot):
    """Если кассир отвечает на сообщение с заказом — отправить текст/фото клиенту в WA."""
    if msg.from_user.id not in CASHIER_TG_IDS and msg.chat.id not in CASHIER_TG_IDS:
        return
    original_id = msg.reply_to_message.message_id
    phone = _order_msg_map.get(original_id)
    if not phone:
        return

    # Если ТЕКСТ
    if msg.text:
        await wz.send_message(phone, msg.text)
        await msg.answer("✅ Текст отправлен в WhatsApp")
    
    # Если ФОТО
    elif msg.photo:
        photo = msg.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{CASHIER_BOT_TOKEN}/{file_info.file_path}"
        await wz.send_image(phone, file_url, caption="Фото вашего заказа! 🌯")
        await msg.answer("✅ Фото отправлено в WhatsApp")


# ── MARK AS COMPLETED (кассир нажал завершить) ────────────

@router.callback_query(F.data.startswith("complete_"))
async def complete_order(cb: CallbackQuery):
    parts = cb.data.split("_")
    order_id = int(parts[1])
    phone = parts[2] if len(parts) > 2 else None
    
    db.update_order_status(order_id, "completed")
    if phone:
        db.set_user_paused(phone, False)
        
        # Сообщение с просьбой об отзыве 2ГИС
        success_msg = (
            "Спасибо за заказ! Приходите к нам еще! 🌯\n\n"
            "Нам очень важно ваше мнение. Пожалуйста, оставьте отзыв о нас в 2ГИС, "
            "это поможет нам стать лучше: https://2gis.kz/aktobe/firm/70000001101359951 ⭐"
        )
        await wz.send_message(phone, success_msg)

    original_text = get_ord_text(cb.message)
    for tag in [
        "<b>✅ ПРИНЯТ В РАБОТУ</b>", "✅ ПРИНЯТ В РАБОТУ",
        "<b>❌ ОТКЛОНЁН</b>", "❌ ОТКЛОНЁН", 
        "<b>🛵 Ждём курьера...</b>", "🛵 Ждём курьера...", 
        "<b>✅ ВЫПОЛНЕН</b>", "✅ ВЫПОЛНЕН"
    ]:
        original_text = original_text.replace(tag, "")
    original_text = original_text.strip()

    # Убираем ВСЕ кнопки
    await edit_ord_msg(
        cb.message,
        original_text + "\n\n<b>✅ ВЫПОЛНЕН</b>",
        reply_markup=None
    )
    await cb.answer("Заказ завершён и ИИ разблокирован!")


# ── ЗАПУСК ────────────────────────────────────────────────

async def main():
    bot = Bot(token=CASHIER_BOT_TOKEN, default=None)
    dp  = Dispatcher()
    dp.include_router(router)
    logger.info("Cashier bot started")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
