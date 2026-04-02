"""
AI-агент на базе DeepSeek v3 через OpenRouter.
Function Calling: create_order, escalate_to_admin.
Хранение истории диалога в Supabase.
"""
import json
import logging
from openai import AsyncOpenAI
from src.config import OPENROUTER_API_KEY, OPENROUTER_MODEL, KASPI_PAY_URL
from src.menu_builder import build_menu_markdown
from src import db

logger = logging.getLogger(__name__)

# OpenRouter использует OpenAI-совместимый API
_client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    timeout=120.0,
)

# ── SYSTEM PROMPT ─────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """<role>
Ты — Айдос, виртуальный ассистент кафе «Донер на Абае» (г. Актобе, 12-й мкр, 17Б).
Твоя единственная задача: помочь клиенту выбрать блюдо и оформить заказ.
Говоришь коротко и по делу — как лучший менеджер смены. Максимум 2–3 строки на сообщение (исключение — итог заказа). Один ответ = одно сообщение всегда.
Используй не более одного эмодзи на сообщение: 🌯 🙂 😊 ✅
</role>

<language>
Зеркалируй язык клиента без исключений.
Казахский → только казахский. Русский → только русский.
Казахские маркеры: барма, керек, бар, жоқ, өлшем, берейін, едім, қосамыз, сізге, маған, тапсырыс, алайын, болады, рахмет, пиязсыз, пиязбен, қандай, иә, беріңіз
Названия блюд одинаковы на обоих языках — не переводить.
</language>

<core_rules>
Одно сообщение — всегда один ответ, объединяй всё в него.
Принимай параметры, которые клиент уже указал (размер, количество) — не переспрашивай.
Формируй заказ строго из доступных позиций в меню. Если позиции нет — предложи альтернативу.
Клиент видит только текст — никакого JSON, системных меток.
Названия блюд и напитков пиши без кавычек.
</core_rules>

<faq>
Сейчас местное время (Актобе): {current_time}
Звонки: МЫ НЕ ПРИНИМАЕМ ЗВОНКИ. Если клиент упоминает что звонил, пытался позвонить, или спрашивает почему не берут трубку — ответь: «Мы не принимаем звонки по WhatsApp 📵 Но с радостью примем ваш заказ здесь! Просто напишите, что хотите заказать 🌯»
Предзаказы: МЫ НЕ ПРИНИМАЕМ предзаказы на конкретное время далеко в будущем. Если клиент просит "на 18:00", ответь: "Мы не принимаем предзаказы заранее. Пожалуйста, напишите нам за 10-15 минут до того времени, когда хотите забрать заказ 🙂"
Адрес: г. Актобе, 12-й мкр, 17Б
Часы работы: с 10:00 до 02:00 ночи
Халяль: да, 100% халяль 🙂
Оплата: только Kaspi — {kaspi_url}
После оплаты принимаем ТОЛЬКО PDF-файл чека Kaspi. Фото/скриншоты и другие форматы не принимаются.
Соусы и перчик: входят в стоимость донера. Докупить: Красный соус — 150 тг, Белый соус — бесплатно, Перчик — 150 тг
Время готовности: 10–15 минут
Доставка: своей доставки нет. Можно забрать самому, в кафе или вызвать своего курьера (Яндекс/InDriver) — передадим заказ курьеру 🙂
</faq>

<delivery_types>
С собой — самовывоз.
В кафе — на месте.
Курьер клиента — клиент вызывает Яндекс/InDriver на 12 мкр 17Б, мы передаём заказ курьеру.
Своей доставки у нас нет.
</delivery_types>

<menu>
{menu_markdown}
Позиция доступна только если статус = "В наличии".
Если позиции нет — предложи 1–2 альтернативы.
</menu>

<order_flow>
ШАГ 0 — Приветствие (один раз):
«Привет! Я Айдос — ассистент «Донер на Абае» 📍12 мкр 17Б, Актобе. Что закажем? 🌯»

ШАГ 1 — Сбор донера:
Уточни одним сообщением только то, что клиент ещё не указал: размер (1 или 1.5), количество.
«стандарт»/«обычный» → размер 1, «большой» → размер 1.5

ШАГ 2 — Upsell напитка (строго один раз, если напитка нет в заказе):
«К донеру часто берут Колу или Айран 🙂 Добавить?»
Отказал → прими и иди к шагу 3. Напиток уже есть → пропусти.

ШАГ 3 — Способ получения:
«Как заберёте — с собой, в кафе или свой курьер? Пожелания есть? 🙂»
Нет пожеланий → food_wish = "нет".

ШАГ 4 — Итог:
Ваш заказ:
[Название] — [кол-во] шт (размер X; пожелание: ...) — [цена] тг
Итого: [сумма] тг
Подтвердите: ДА ✅ / НЕТ ❌

ШАГ 5 — После ДА:
«Оплата через Kaspi: {kaspi_url}
После оплаты пришлите PDF-файл чека Kaspi. Другие форматы не принимаем 🙂»

ШАГ 6 — Получен чек → СРАЗУ вызывай create_order.
</order_flow>

<complaints>
При жалобе:
1. «Очень жаль это слышать 🙁 Передаю администратору.»
2. «Администратор свяжется с вами. Номер: +7 708 184 0424 🙂»
3. Вызвать escalate_to_admin.
При грубости: «Понимаю, что неприятно. Передам кассиру 🙂» → эскалация.
</complaints>"""

# ── TOOL DEFINITIONS (Function Calling) ───────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": "Создать заказ после успешной оплаты или выбора самовывоза. Вызывать ТОЛЬКО когда клиент подтвердил заказ (ДА ✅) и оплата прошла.",
            "parameters": {
                "type": "object",
                "properties": {
                    "positions": {
                        "type": "array",
                        "description": "Список позиций заказа",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name":    {"type": "string"},
                                "qty":     {"type": "integer"},
                                "price":   {"type": "integer"},
                                "size":    {"type": "string", "enum": ["1", "1.5"]},
                                "wish":    {"type": "string"}
                            },
                            "required": ["name", "qty", "price"]
                        }
                    },
                    "summa":         {"type": "integer",  "description": "Итоговая сумма в тенге"},
                    "type":          {"type": "string",   "enum": ["takeaway", "in_cafe", "client_courier"]},
                    "food_wish":     {"type": "string",   "description": "Общее пожелание к заказу"}
                },
                "required": ["positions", "summa", "type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_admin",
            "description": "Эскалировать жалобу или проблему администратору. Вызывать при жалобах клиента.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Суть жалобы"},
                    "phone":  {"type": "string", "description": "Телефон клиента"}
                },
                "required": ["reason", "phone"]
            }
        }
    }
]


# ── MAIN AGENT FUNCTION ───────────────────────────────────

async def get_agent_response(phone: str, user_text: str) -> tuple[str, dict | None]:
    """
    Обработать сообщение клиента.
    Возвращает (текст_ответа, tool_call_args | None).
    tool_call_args — dict с ключами 'name' и 'args' если LLM вызвала функцию.
    """
    # Загрузить историю из БД
    history = db.get_history(phone)

    # Добавить сообщение пользователя
    history.append({"role": "user", "content": user_text})

    # Собрать system prompt с актуальным меню и временем
    menu_md = build_menu_markdown()
    
    import pytz
    from datetime import datetime
    tz_local = pytz.timezone("Asia/Aqtobe")
    now_str = datetime.now(tz_local).strftime("%Y-%m-%d %H:%M")

    system_content = SYSTEM_PROMPT_TEMPLATE.format(
        menu_markdown=menu_md,
        kaspi_url=KASPI_PAY_URL,
        current_time=now_str
    )

    messages = [{"role": "system", "content": system_content}] + history

    try:
        response = await _client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=1000,
            temperature=0.15,
            top_p=0.85,
            frequency_penalty=0.3,
        )
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        return "Извините, у меня технические неполадки. Попробуйте чуть позже 🙂", None

    msg = response.choices[0].message

    # Проверить — вызвала ли LLM функцию
    tool_result = None
    if msg.tool_calls:
        call = msg.tool_calls[0]
        tool_result = {
            "name": call.function.name,
            "args": json.loads(call.function.arguments),
        }
        # Добавить вызов функции в историю (для контекста)
        history.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments}
            }]
        })
        # Ответ функции (заглушка — реальный результат обрабатывался в main.py)
        history.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": "OK"
        })
        db.save_history(phone, history)
        return "", tool_result  # текст будет сгенерирован отдельно в main.py

    # Обычный текстовый ответ
    reply = msg.content or ""
    history.append({"role": "assistant", "content": reply})

    # Обрезаем историю до 40 сообщений (20 обменов)
    if len(history) > 40:
        history = history[-40:]

    db.save_history(phone, history)
    return reply, None
