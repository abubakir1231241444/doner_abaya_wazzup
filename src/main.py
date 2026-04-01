"""
FastAPI — главный webhook-сервер.
Принимает сообщения от Wazzup (WhatsApp) и маршрутизирует их через AI-агент.

Формат вебхука Wazzup v3:
{
  "messages": [
    {
      "messageId": "...",
      "channelId": "...",
      "chatType": "whatsapp",
      "chatId": "77012345678",   ← номер телефона (без +)
      "type": "text",             ← text | image | document | audio | video | ...
      "text": "Привет",           ← только для type=text
      "contentUri": "https://...",← только для медиа/файлов
      "dateTime": 1234567890000,
      "authorType": "client"      ← client | manager | bot
    }
  ]
}
"""
import json
import logging
import asyncio
import time
import pytz
from datetime import datetime

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from src.config import TZ, WORK_HOUR_OPEN, WORK_HOUR_CLOSE, WEBHOOK_SECRET, ALLOWED_PHONES
from src import db
from src import wazzup as wz
from src.ai_agent import get_agent_response
from src.pdf_validator import validate_receipt
from src.order_tools import handle_create_order, handle_escalate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Донер на Абае — Wazzup Bot")

tz_local = pytz.timezone(TZ)

# ── ИН-МЕМОРИ СТРУКТУРЫ ───────────────────────────────────
# Дедубликация сообщений: messageId → timestamp
_recent_messages: dict[str, float] = {}

# Ожидание оплаты: phone → {order_total, order_args}
_pending_payment: dict[str, dict] = {}

# Чеки которые пришли до того как AI вызвал create_order
_valid_receipts_passed: dict[str, float] = {}

# Временное хранилище PDF-байтов
_pending_receipts: dict[str, tuple[float, bytes]] = {}

# Debounce — собираем несколько сообщений подряд в одно
_debounce_buffer: dict[str, list[str]] = {}
_debounce_tasks: dict[str, asyncio.Task] = {}

# Follow-up таймеры (второе касание через 5 мин)
_followup_tasks: dict[str, asyncio.Task] = {}
_followed_up_users: set[str] = set()

FOLLOWUP_DELAY = 5 * 60   # секунды
DEBOUNCE_TIME  = 10.0     # секунды


# ── ОЧИСТКА ПАМЯТИ ────────────────────────────────────────

async def memory_cleanup_loop():
    """Раз в час чистим старые записи из ин-мемори словарей."""
    while True:
        await asyncio.sleep(3600)
        now = time.time()
        for k in list(_recent_messages.keys()):
            if now - _recent_messages[k] > 3600:
                del _recent_messages[k]
        for k in list(_valid_receipts_passed.keys()):
            if now - _valid_receipts_passed[k] > 3600:
                del _valid_receipts_passed[k]
        for k in list(_pending_receipts.keys()):
            ts, _ = _pending_receipts[k]
            if now - ts > 3600:
                del _pending_receipts[k]
        logger.info("Memory cleanup performed.")


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(memory_cleanup_loop())


# ── DEBOUNCE ──────────────────────────────────────────────

async def _debounced_worker(phone: str):
    """Ждёт DEBOUNCE_TIME после последнего сообщения, затем обрабатывает все разом."""
    try:
        await asyncio.sleep(DEBOUNCE_TIME)
        texts = _debounce_buffer.pop(phone, [])
        _debounce_tasks.pop(phone, None)

        if not texts:
            return

        combined_text = "\n".join(texts)
        logger.info(f"Debounced: {len(texts)} сообщений от {phone}: {combined_text[:100]}")
        await process_message(phone, combined_text, None, None)
    except asyncio.CancelledError:
        pass


# ── FOLLOW-UP (второе касание) ────────────────────────────

async def _followup_worker(phone: str):
    """Через 5 мин отправляет персональное AI-напоминание если заказ не оформлен."""
    try:
        logger.info(f"Follow-up timer started for {phone} ({FOLLOWUP_DELAY}s)")
        await asyncio.sleep(FOLLOWUP_DELAY)

        if db.is_user_paused(phone) or phone in _followed_up_users:
            return

        history = db.get_history(phone)
        if not history:
            return

        # Защита от случайного срабатывания после завершения/отклонения заказа
        # Если последний контекст содержит метку системы об успешном заказе, не спамим.
        last_content = history[-1]['content']
        if "СИСТЕМА: Заказ #" in last_content:
            return

        last_msgs = history[-5:]
        context = "\n".join([f"{m['role']}: {m['content']}" for m in last_msgs])

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
                    "ЗАПРЕЩЕНО здороваться. "
                    "Клиент замолчал и не завершил заказ. "
                    "Напиши ОДНО короткое дружелюбное напоминание (1 предложение) "
                    "связанное с тем, на чём остановился диалог. "
                    "Используй один уместный эмодзи."
                )
            }, {
                "role": "user",
                "content": f"История диалога:\n{context}\n\nНапоминание без приветствия:"
            }],
            temperature=0.7,
            max_tokens=100,
        )

        followup_text = resp.choices[0].message.content.strip()
        if followup_text:
            _followed_up_users.add(phone)
            await wz.send_message(phone, followup_text)
            logger.info(f"Follow-up sent to {phone}: {followup_text[:50]}")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"Follow-up error for {phone}: {e}")
    finally:
        _followup_tasks.pop(phone, None)


def _schedule_followup(phone: str):
    """Перезапустить таймер второго касания."""
    old = _followup_tasks.pop(phone, None)
    if old:
        old.cancel()
    _followup_tasks[phone] = asyncio.create_task(_followup_worker(phone))


def _cancel_followup(phone: str):
    """Отменить таймер (заказ оформлен)."""
    old = _followup_tasks.pop(phone, None)
    if old:
        old.cancel()
    _followed_up_users.discard(phone)


# ── РАБОЧИЕ ЧАСЫ ─────────────────────────────────────────

def is_working_hours() -> bool:
    """Проверить, работает ли кафе (10:00–01:00)."""
    import pytz
    from datetime import datetime
    tz_local = pytz.timezone("Asia/Aqtobe")
    now = datetime.now(tz_local)
    h = now.hour
    
    # С 10:00 до 23:59
    if 10 <= h <= 23:
        return True
    # С 00:00 до 00:59 (то есть до 01:00)
    if 0 <= h < 1:
        return True
    return False


# ── РАЗБОР ВЕБХУКА WAZZUP ─────────────────────────────────

def extract_message(data: dict) -> tuple[str, str, str | None, str | None, str]:
    """
    Разбирает один объект сообщения из массива messages вебхука Wazzup v3.

    Wazzup webhook payload:
    {
      "messages": [
        {
          "messageId": "uuid",
          "channelId": "uuid",
          "chatType": "whatsapp",
          "chatId": "77012345678",
          "type": "text" | "image" | "document" | "audio" | "video" | ...,
          "text": "...",          — только если type == text
          "contentUri": "...",    — URL файла (PDF-чек, фото и т.д.)
          "dateTime": 1234567890000,
          "authorType": "client" | "manager" | "bot"
        }
      ]
    }

    Возвращает: (phone, text, file_url, message_id, author_type, msg_type)
    """
    try:
        phone       = str(data.get("chatId", "")).lstrip("+")
        message_id  = data.get("messageId", "")
        text        = data.get("text", "") or ""
        content_uri = data.get("contentUri") or None
        author_type = data.get("authorType", "client")
        msg_type    = data.get("type", "text")

        # Для файлов без текста — file_url
        file_url = None
        if msg_type in ("document", "image", "audio", "video", "file") and content_uri:
            file_url = content_uri

        # Если есть quoted (ответ на сообщение) — ищем исходный текст в БД
        quoted_msg = data.get("quotedMessage")
        if isinstance(quoted_msg, dict):
            quoted_id = quoted_msg.get("messageId")
            if quoted_id:
                original_text = db.get_message_text(quoted_id)
                if original_text:
                    text = f'[Клиент ответил на наше сообщение: "{original_text}"]\n{text}'
                else:
                    text = f'[Клиент ответил на наше предыдущее сообщение]\n{text}'

        logger.info(
            f"Wazzup msg: phone={phone}, type={msg_type}, "
            f"author={author_type}, text='{text[:80]}', "
            f"file_url={'yes' if file_url else 'no'}, id={message_id}"
        )
        return phone, text.strip(), file_url, message_id, author_type, msg_type

    except Exception as e:
        logger.error(f"extract_message error: {e} | data={data}")
        return "", "", None, None, "client", "text"


# ── ОСНОВНАЯ ЛОГИКА ОБРАБОТКИ ─────────────────────────────

async def process_message(phone: str, text: str, file_url: str | None, message_id: str | None, msg_type: str = "text"):
    """Основная логика обработки входящего сообщения от клиента."""

    # 1. Проверка рабочих часов
    if not is_working_hours():
        await wz.send_message(phone, "Мы сейчас закрыты 🙁 Работаем с 10:00 до 01:00 ночи. Ждём вас в рабочее время! 🌯")
        return

    # 2. Клиент уже оформил заказ (пауза) — пересылаем дополнение кассиру
    if db.is_user_paused(phone):
        if text:
            from src.order_tools import _send_telegram
            msg = f"⚠️ <b>Дополнение от клиента +{phone}</b>\n\nТекст: {text}"
            await _send_telegram(msg)
            await wz.send_message(phone, "Передал ваше дополнение к заказу кассиру ⏳")
        logger.info(f"{phone}: user is paused, message forwarded to cashier")
        return

    # 3. Клиент прислал файл — АУДИО или PDF чек
    if file_url:
        file_bytes = await wz.download_file(file_url)

        if msg_type in ("audio", "voice"):
            from src.transcribe import transcribe_audio
            transcription = await transcribe_audio(file_bytes)
            if not transcription:
                await wz.send_message(phone, "Извините, не смог разобрать голосовое сообщение 🙁 Напишите текстом, пожалуйста.")
                return
            # Меняем text на транскрипцию и идём дальше к AI
            text = transcription
            await wz.send_message(phone, f"🎤 _{text}_")
            file_url = None  # Сбрасываем URL, это не чек
        else:
            # Чек об оплате (PDF или фото)
            if phone in _pending_payment:
                pending = _pending_payment.pop(phone)
                order_id, _ = await handle_create_order(phone, pending["order_args"], receipt_bytes=file_bytes)
                _cancel_followup(phone)
                db.set_user_paused(phone, True)
                history = db.get_history(phone)
                history.append({
                    "role": "user",
                    "content": f"[СИСТЕМА: Заказ #{order_id} оформлен на сумму {pending['order_total']}. Ожидайте готовность.]"
                })
                if len(history) > 40:
                    history = history[-40:]
                db.save_history(phone, history)
                await wz.send_message(phone, "Ваш чек передан кассиру на проверку ⏳\nМы сообщим, как только заказ будет готов 🙂")
            else:
                # Чек прислали ещё до того как AI запросил оплату
                _pending_receipts[phone] = (time.time(), file_bytes)
                _valid_receipts_passed[phone] = time.time()
                text = f"[СИСТЕМА: Клиент прислал чек Kaspi. СРОЧНО вызови инструмент create_order!] {text}"
            # Продолжаем обработку текста ниже

    # 4. Если нет текста — выходим
    if not text:
        return

    # 5. Флаг наличия чека
    has_valid_receipt = "[СИСТЕМА: Клиент прислал чек" in text

    # 6. AI-агент
    reply, tool_call = await get_agent_response(phone, text)

    if tool_call:
        name = tool_call["name"]
        args = tool_call["args"]

        if name == "create_order":
            # Ищем чек в буфере
            pdf_bytes_to_pass = None
            if _pending_receipts.get(phone):
                ts, pb = _pending_receipts.pop(phone)
                pdf_bytes_to_pass = pb

            if has_valid_receipt or phone in _valid_receipts_passed or pdf_bytes_to_pass:
                # Чек есть — оформляем сразу
                order_id, _ = await handle_create_order(phone, args, receipt_bytes=pdf_bytes_to_pass)
                _cancel_followup(phone)
                db.set_user_paused(phone, True)
                history = db.get_history(phone)
                history.append({
                    "role": "user",
                    "content": f"[СИСТЕМА: Заказ #{order_id} оформлен. Ожидайте готовности/доставки.]"
                })
                if len(history) > 40:
                    history = history[-40:]
                db.save_history(phone, history)
                if phone in _valid_receipts_passed:
                    del _valid_receipts_passed[phone]
                await wz.send_message(phone, "Чек передан кассиру на проверку ⏳ Кассир подтвердит заказ в ближайшее время.")
            else:
                # Чека нет — просим оплатить
                _pending_payment[phone] = {
                    "order_total": args["summa"],
                    "order_args":  args,
                }
                from src.config import KASPI_PAY_URL
                await wz.send_message(
                    phone,
                    f"Оплата через Kaspi: {KASPI_PAY_URL}\nПосле оплаты пришлите чек в PDF 🙂"
                )

        elif name == "escalate_to_admin":
            await handle_escalate(phone, args)
            await wz.send_message(
                phone,
                "Спасибо, уже передаю. Администратор свяжется с вами в ближайшие минуты. Номер: +7 777 589 20 72 🙂"
            )
    else:
        # Обычный текстовый ответ от LLM — одно сообщение
        if reply:
            await wz.send_message(phone, reply)

    # Запускаем follow-up если заказ ещё не оформлен
    if not db.is_user_paused(phone):
        _schedule_followup(phone)


# ── ENDPOINTS ─────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "provider": "wazzup"}


@app.post("/webhook")
async def handle_webhook(request: Request, bg_tasks: BackgroundTasks):
    """
    Принимает входящие сообщения от Wazzup v3.

    Wazzup верифицирует URL, отправив { "test": true } — отвечаем 200.
    Реальные сообщения приходят в поле "messages": [...].

    Wazzup добавляет заголовок Authorization: Bearer {crmKey} если настроен.
    Мы используем параметр ?secret= для дополнительной безопасности.
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    # Верификационный пинг от Wazzup при регистрации вебхука
    if data.get("test") is True:
        logger.info("Wazzup webhook verification ping received")
        return JSONResponse({"ok": True})

    messages = data.get("messages", [])
    if not messages:
        logger.info("Wazzup webhook: no messages in payload")
        return JSONResponse({"ok": True})

    logger.info(f"Wazzup webhook: {len(messages)} message(s) received")

    for msg in messages:
        phone, text, file_url, message_id, author_type, msg_type = extract_message(msg)

        # Игнорируем ВСЕ не-client сообщения (manager, bot, system и т.д.)
        if author_type != "client":
            logger.info(f"Ignoring non-client message (authorType={author_type}) for {phone}")
            if message_id and text:
                db.save_wazzup_message(message_id, phone, text, True)
            continue

        # Эхо-детекция (хеш текста + кулдаун после отправки)
        if text and wz.is_echo(phone, text):
            logger.info(f"Ignoring echo from {phone}")
            if message_id:
                db.save_wazzup_message(message_id, phone, text, True)
            continue

        # Дедубликация по message_id
        if message_id and message_id in _recent_messages:
            logger.info(f"Duplicate messageId {message_id}, ignoring")
            continue
        if message_id:
            _recent_messages[message_id] = time.time()

        if not phone:
            logger.warning("No phone extracted from webhook message")
            continue

        # ФИЛЬТР: Отвечаем только разрешенным номерам (для тестов)
        if ALLOWED_PHONES and phone not in ALLOWED_PHONES:
            logger.info(f"Phone {phone} not in ALLOWED_PHONES, ignoring.")
            continue

        # Файлы обрабатываем немедленно (без debounce)
        if file_url:
            bg_tasks.add_task(process_message, phone, text, file_url, message_id, msg_type)
            continue

        # Текстовые сообщения — debounce
        if text:
            if phone not in _debounce_buffer:
                _debounce_buffer[phone] = []
            _debounce_buffer[phone].append(text)

            # Перезапускаем таймер при каждом новом сообщении
            old_task = _debounce_tasks.pop(phone, None)
            if old_task:
                old_task.cancel()
            _debounce_tasks[phone] = asyncio.create_task(_debounced_worker(phone))
            logger.info(f"Debounce reset for {phone}, buffer: {len(_debounce_buffer[phone])} msg(s)")

    return JSONResponse({"ok": True})


@app.post("/internal/resume/{phone}")
async def resume_user(phone: str):
    """Снять паузу с клиента (вызывается ботом кассира после закрытия заказа)."""
    db.set_user_paused(phone, False)
    return {"ok": True, "phone": phone}


@app.post("/internal/register-webhook")
async def register_webhook_endpoint(request: Request):
    """
    Зарегистрировать URL вебхука в Wazzup.
    Body: { "url": "https://your-tunnel.trycloudflare.com/webhook" }
    """
    try:
        body = await request.json()
        webhook_url = body.get("url", "")
        if not webhook_url:
            raise HTTPException(status_code=400, detail="url is required")
        ok = await wz.register_webhook(webhook_url)
        return {"ok": ok, "registered_url": webhook_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/internal/webhook-info")
async def webhook_info():
    """Посмотреть текущий зарегистрированный вебхук в Wazzup."""
    info = await wz.get_webhook_info()
    return info
