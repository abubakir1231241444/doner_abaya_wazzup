"""
FastAPI — главный webhook-сервер.
Принимает сообщения от SendPulse (WhatsApp) и маршрутизирует их.
"""
import json
import logging
import asyncio
from datetime import datetime

import time
import pytz
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from src.config import TZ, WORK_HOUR_OPEN, WORK_HOUR_CLOSE, SENDPULSE_BOT_ID, WEBHOOK_SECRET
from src import db, sendpulse as sp
from src.ai_agent import get_agent_response
from src.pdf_validator import validate_receipt
from src.order_tools import handle_create_order, handle_escalate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

tz_local = pytz.timezone(TZ)

# ── ИН-МЕМОРИ СТРУКТУРЫ (С ОЧИСТКОЙ) ──────────────────────
# Теперь храним время добавления для очистки
_recent_messages: dict[str, float] = {}  
_user_tasks: dict[str, asyncio.Task] = {} # This variable is not used in the provided diff, but was added.
# Временное хранилище ожидаемых сумм для PDF-валидации
# phone → {"order_total": int, "order_args": dict}
_pending_payment: dict[str, dict] = {}

# Хранилище телефонов, которые уже прислали валидный чек, 
# но LLM еще не успела вызвать create_order
_valid_receipts_passed: dict[str, float] = {}

# Временное хранилище чеков (в байтах) на случай если клиент 
# присылает чек до/во время генерации заказа ИИ
_pending_receipts: dict[str, tuple[float, bytes]] = {}

# Очередь для debouncing сообщений
_debounce_buffer: dict[str, list[str]] = {}
_debounce_tasks: dict[str, asyncio.Task] = {}

# Follow-up таймеры (второе касание)
_followup_tasks: dict[str, asyncio.Task] = {}
_followed_up_users: set[str] = set()
FOLLOWUP_DELAY = 5 * 60  # 5 минут
DEBOUNCE_TIME = 10.0

async def memory_cleanup_loop():
    while True:
        await asyncio.sleep(3600)  # Раз в час
        now = time.time()
        # Очистка сообщений старше 1 часа
        for k in list(_recent_messages.keys()):
            if now - _recent_messages[k] > 3600:
                del _recent_messages[k]
        # Очистка пропущенных чеков
        for k in list(_valid_receipts_passed.keys()):
            if now - _valid_receipts_passed[k] > 3600:
                del _valid_receipts_passed[k]
        # Очистка висящих чеков (PDF)
        for k in list(_pending_receipts.keys()):
            ts, _ = _pending_receipts[k]
            if now - ts > 3600:
                del _pending_receipts[k]
        logger.info("Memory cleanup performed.")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(memory_cleanup_loop())


async def _debounced_worker(phone: str, contact_id: str):
    """Ждет 3.5 секунды после последнего сообщения, затем обрабатывает всё вместе."""
    try:
        await asyncio.sleep(DEBOUNCE_TIME)  # окно сбора — если придёт новое сообщение, таймер перезапустится
        texts = _debounce_buffer.pop(phone, [])
        _debounce_tasks.pop(phone, None)
        
        if not texts:
            return
            
        combined_text = "\n".join(texts)
        logger.info(f"Debounced: собрано {len(texts)} сообщений от {phone}: {combined_text[:100]}")
        await process_message(phone, combined_text, contact_id, None, None)
    except asyncio.CancelledError:
        pass  # нормально — таймер сброшен новым сообщением


async def _followup_worker(phone: str):
    """Через 5 мин после последнего сообщения отправляет персональное напоминание через OpenRouter."""
    try:
        logger.info(f"Follow-up worker started for {phone}, sleeping for {FOLLOWUP_DELAY}s")
        await asyncio.sleep(FOLLOWUP_DELAY)
        
        # Проверяем не оформил ли клиент заказ
        if db.is_user_paused(phone) or phone in _followed_up_users:
            return
        
        # Получаем историю диалога
        history = db.get_history(phone)
        if not history:
            return
            
        last_msgs = history[-5:]  # последние 5 сообщений
        context = "\n".join([f"{m['role']}: {m['content']}" for m in last_msgs])
        
        # Мини-агент через OpenRouter (конфигурируемая модель)
        from openai import AsyncOpenAI
        from src.config import OPENROUTER_API_KEY, OPENROUTER_MODEL
        
        client = AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            timeout=30.0,
        )
        
        resp = await client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[{
                "role": "system",
                "content": (
                    "Ты — дружелюбный бот донерной 'Донер на Абае'. "
                    "ВНИМАНИЕ: КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО здороваться (никаких 'Привет', 'Сәлем', 'Здравствуйте'). "
                    "Клиент начал диалог, но замолчал и не завершил заказ. "
                    "Напиши ОДНО короткое дружелюбное напоминание (строго 1 предложение) "
                    "связанное с тем, на чем клиент остановился в диалоге. Сразу переходи к делу. "
                    "Используй один уместный эмодзи."
                )
            }, {
                "role": "user",
                "content": f"Вот история диалога:\n{context}\n\nНапиши напоминание без приветствия:"
            }],
            temperature=0.7,
            max_tokens=100,
        )
        
        followup_text = resp.choices[0].message.content.strip()
        
        if followup_text:
            _followed_up_users.add(phone)
            await sp.send_message(phone, followup_text)
            logger.info(f"Follow-up sent to {phone}: {followup_text[:50]}")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"Follow-up error for {phone}: {e}")
    finally:
        _followup_tasks.pop(phone, None)


def _schedule_followup(phone: str):
    """Перезапустить таймер второго касания."""
    # Отменить предыдущий таймер если был
    old = _followup_tasks.pop(phone, None)
    if old:
        old.cancel()
    _followup_tasks[phone] = asyncio.create_task(_followup_worker(phone))


def _cancel_followup(phone: str):
    """Отменить таймер второго касания (заказ оформлен)."""
    old = _followup_tasks.pop(phone, None)
    if old:
        old.cancel()
    _followed_up_users.discard(phone)


def is_working_hours() -> bool:
    """Проверить, работает ли кафе сейчас (10:00–01:00 UTC+5)."""
    # Для тестов всегда True
    return True
    
    # Оригинальная логика:
    # now = datetime.now(tz_local)
    # h = now.hour
    # if WORK_HOUR_OPEN <= h <= 23:
    #     return True
    # if 0 <= h < WORK_HOUR_CLOSE:
    #     return True
    # return False


def extract_message(data) -> tuple[str, str, str, str | None, str | None]:
    """
    Извлечь из webhook-payload SendPulse.
    Текст в: contact.last_message ИЛИ info.message.channel_data.message.text.body
    """
    try:
        if isinstance(data, list):
            if not data:
                return "", "", "", None, None
            data = data[0]

        contact = data.get("contact", {})
        phone = contact.get("phone", "").lstrip("+")
        contact_id = contact.get("id", "")
        message_id = data.get("message", {}).get("id")
        if not message_id:
            try:
                message_id = data["info"]["message"].get("id") or data["info"]["message"]["channel_data"]["message"].get("id")
            except (KeyError, TypeError):
                pass

        text = ""
        file_url = None

        # 1) contact.last_message
        text = contact.get("last_message", "") or ""

        # 2) info.message.channel_data.message.text.body
        if not text:
            try:
                text_obj = data["info"]["message"]["channel_data"]["message"]["text"]
                if isinstance(text_obj, dict):
                    text = text_obj.get("body", "") or ""
                elif isinstance(text_obj, str):
                    text = text_obj
            except (KeyError, TypeError):
                pass

        # 3) Fallback
        if not text:
            message = data.get("message", {})
            if isinstance(message, dict):
                text = message.get("text", "") or ""

        # Файл (PDF чек)
        try:
            ch_msg = data["info"]["message"]["channel_data"]["message"]
            doc = ch_msg.get("document") or ch_msg.get("file") or {}
            if isinstance(doc, dict):
                file_url = doc.get("url") or doc.get("link")
        except (KeyError, TypeError):
            pass

        # Достаем контекст (на какое сообщение ответил пользователь)
        quoted_text = ""
        try:
            ch_msg = data["info"]["message"]["channel_data"]["message"]
            context_obj = ch_msg.get("context", {})
            if isinstance(context_obj, dict):
                quoted_payload = context_obj.get("quoted_payload", {})
                if "text" in quoted_payload:
                    quoted_text = quoted_payload["text"]
                elif "body" in quoted_payload:
                    quoted_text = quoted_payload["body"]
        except (KeyError, TypeError):
            pass

        if text and quoted_text:
            text = f'[Клиент ответил на наше сообщение: "{quoted_text}"]\n{text}'

        # Достаем направление (in/out)
        direction = "in"
        try:
            direction = data["info"]["message"]["channel_data"]["message"].get("direction", "in")
        except (KeyError, TypeError):
            pass

        logger.info(f"Extracted: phone={phone}, text='{text[:80]}', direction='{direction}', message_id={message_id}")
        return phone, text.strip(), contact_id, file_url, message_id, direction
    except Exception as e:
        logger.error(f"extract_message error: {e} | data={data}")
        return "", "", "", None, None, "in"


async def process_message(phone: str, text: str, contact_id: str, file_url: str | None, message_id: str | None):
    """Основная логика обработки входящего сообщения."""

    # 1. Проверка рабочих часов (ОТКЛЮЧЕНО ДЛЯ ТЕСТОВ)
    # if not is_working_hours():
    #     await sp.send_message(phone, "Мы закрыты 🙁 Работаем с 10:00 до 1:00 ночи. Ждём вас! 🌯")
    #     return

    # 2. Пауза — клиент уже оформил заказ
    if db.is_user_paused(phone):
        if text:
            # Пересылаем сообщение кассиру
            from src.order_tools import _send_telegram
            msg = f"⚠️ <b>Дополнение от клиента +{phone}</b>\n\nТекст: {text}"
            await _send_telegram(msg)

            await sp.send_message(phone, "Передал ваше дополнение к заказу кассиру ⏳")
        logger.info(f"{phone}: user is paused, message forwarded to cashier")
        return

    # 3. Если клиент прислал файл — чек об оплате
    if file_url:
        await sp.send_typing(contact_id)
        pdf_bytes = await sp.download_file(file_url)
        
        if phone in _pending_payment:
            pending = _pending_payment.pop(phone)
            order_id, _ = await handle_create_order(phone, pending["order_args"], receipt_bytes=pdf_bytes)
            _cancel_followup(phone)
            db.set_user_paused(phone, True)
            # Ставим бота на паузу на 60 минут
            await sp.pause_automation(contact_id=contact_id, minutes=60)
            # Добавляем системное сообщение вместо полного сброса
            history = db.get_history(phone)
            history.append({"role": "user", "content": f"[СИСТЕМА: Заказ #{order_id} оформлен на сумму {pending['order_total']}. Ожидайте доставку/готовность.]"})
            if len(history) > 40: history = history[-40:]
            db.save_history(phone, history)
            
            reply = "Ваш чек передан кассиру на проверку ⏳\nМы сообщим, как только курьер выедет к вам 🙂"
            await sp.send_message(phone, reply)
            return
        else:
            # Чек прислали до того как мы его запросили (или одновременно)
            _pending_receipts[phone] = (time.time(), pdf_bytes)
            _valid_receipts_passed[phone] = time.time()
            text = f"[СИСТЕМА: Клиент прислал чек Kaspi. СРОЧНО вызови инструмент create_order!] {text}"

    # 4. Обычный текст → AI-агент
    if not text:
        return

    # Флаг: был ли в этом сообщении передан чек
    has_valid_receipt = "[СИСТЕМА: Клиент прислал чек" in text

    await sp.send_typing(contact_id)
    reply, tool_call = await get_agent_response(phone, text)

    if tool_call:
        name = tool_call["name"]
        args = tool_call["args"]

        if name == "create_order":
            dtype = args.get("type", "takeaway")
            
            pdf_bytes_to_pass = None
            if _pending_receipts.get(phone):
                ts, pb = _pending_receipts.get(phone)
                pdf_bytes_to_pass = pb
                del _pending_receipts[phone] # Consume the pending receipt
            
            # Проверяем, есть ли чек
            if has_valid_receipt or phone in _valid_receipts_passed or pdf_bytes_to_pass:
                order_id, _ = await handle_create_order(phone, args, receipt_bytes=pdf_bytes_to_pass)
                _cancel_followup(phone)
                db.set_user_paused(phone, True)
                # Ставим бота на паузу на 60 минут
                await sp.pause_automation(contact_id=contact_id, minutes=60)
                # Добавляем системное сообщение вместо полного сброса
                history = db.get_history(phone)
                history.append({"role": "user", "content": f"[СИСТЕМА: Заказ #{order_id} оформлен. Ожидайте готовности/доставки.]"})
                if len(history) > 40: history = history[-40:]
                db.save_history(phone, history)
                
                if phone in _valid_receipts_passed:
                    del _valid_receipts_passed[phone]
                
                confirm = "Чек передан кассиру на проверку ⏳ Кассир подтвердит заказ в ближайшее время."
                await sp.send_message(phone, confirm)
            else:
                # БЕЗ чека — запросить оплату
                _pending_payment[phone] = {
                    "order_total": args["summa"],
                    "order_args":  args,
                }
                from src.config import KASPI_PAY_URL
                await sp.send_message(
                    phone,
                    f"Оплата через Kaspi: {KASPI_PAY_URL}\nПосле оплаты пришлите чек в PDF 🙂"
                )

        elif name == "escalate_to_admin":
            await handle_escalate(phone, args)
            await sp.send_message(
                phone,
                "Спасибо, уже передаю. Администратор свяжется с вами в ближайшие минуты. Номер: +7 777 589 20 72 🙂"
            )
    else:
        # Обычный текстовый ответ от LLM
        if reply:
            parts = reply.split("[SPLIT]")
            for p in parts:
                p = p.strip()
                if p:
                    await sp.send_message(phone, p)
                    await asyncio.sleep(0.5)
    
    # Запускаем таймер follow-up (второе касание)
    # Он будет отменён если клиент напишет снова или закажет
    if not db.is_user_paused(phone):
        _schedule_followup(phone)



# ── ENDPOINTS ─────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
async def handle_webhook(request: Request, bg_tasks: BackgroundTasks, secret: str = None):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized webhook call")
    
    try:
        data = await request.json()
        # Избегаем логирования PII (номера телефонов)
        import copy
        safe_data = copy.deepcopy(data)
        if isinstance(safe_data, list) and safe_data:
            if "contact" in safe_data[0] and "phone" in safe_data[0]["contact"]:
                safe_data[0]["contact"]["phone"] = "***"
        elif isinstance(safe_data, dict) and "contact" in safe_data and "phone" in safe_data["contact"]:
            safe_data["contact"]["phone"] = "***"
        logger.info(f"Webhook payload received (length: {len(json.dumps(safe_data))})")
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    phone, text, contact_id, file_url, message_id, direction = extract_message(data)
    
    # Игнорируем эхо-вебхуки (наши собственные отправленные сообщения)
    if text and text.strip()[:100] in sp.SENT_MESSAGES_CACHE:
        logger.info(f"Ignoring outgoing eco-message to {phone}")
        return JSONResponse({"ok": True})

    if direction == "out":
        logger.info(f"Ignoring outgoing message from {phone}")
        return JSONResponse({"ok": True})
    
    # Дедубликация
    if message_id and message_id in _recent_messages:
        logger.info(f"Duplicate message_id {message_id} from {phone}, ignoring.")
        return JSONResponse(content={"status": "duplicate ignored"}, status_code=200)
    if message_id:
        _recent_messages[message_id] = time.time()

    if not phone:
        logger.warning("No phone extracted from webhook payload")
        return JSONResponse({"ok": True})

    # Если это файл — обрабатываем сразу (без дебаунса)
    if file_url:
        bg_tasks.add_task(process_message, phone, text, contact_id, file_url, message_id)
        return JSONResponse({"ok": True})

    # Дебаунс для текстовых сообщений
    if text:
        if phone not in _debounce_buffer:
            _debounce_buffer[phone] = []
        _debounce_buffer[phone].append(text)
        
        # ОТМЕНЯЕМ старый таймер и ЗАПУСКАЕМ новый при КАЖДОМ сообщении
        old_task = _debounce_tasks.pop(phone, None)
        if old_task:
            old_task.cancel()
        task = asyncio.create_task(_debounced_worker(phone, contact_id))
        _debounce_tasks[phone] = task
        logger.info(f"Дебаунс сброшен, буфер {phone}: {len(_debounce_buffer[phone])} сообщений")

    return JSONResponse({"ok": True})


@app.post("/internal/resume/{phone}")
async def resume_user(phone: str):
    """Внутренний эндпоинт для снятия паузы с клиента (вызывается ботом кассира)."""
    db.set_user_paused(phone, False)
    return {"ok": True, "phone": phone}
