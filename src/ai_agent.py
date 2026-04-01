"""
AI-агент на базе DeepSeek v3 через OpenRouter.
Function Calling: create_order, escalate_to_admin.
Хранение истории диалога в Supabase.
"""
import json
import logging
from openai import AsyncOpenAI
from src.config import OPENROUTER_API_KEY, OPENROUTER_MODEL, KASPI_PAY_URL, DELIVERY_COST
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

SYSTEM_PROMPT_TEMPLATE = """Ты — Айдос, виртуальный ассистент кафе «Донер на Абае» (Актобе).
Твоя задача: помочь выбрать блюдо и оформить заказ.
Коротко (2-3 строки), один ответ = одно сообщение.
Зеркалируй язык клиента (Казахский/Русский).
Всегда используй метку [SPLIT] перед вопросом клиенту.

Адрес: 12-й мкр, 17Б. Работаем: 11:00–03:00.
Халяль: Да. Оплата: Kaspi — {kaspi_url}

<delivery_types>
С собой, В кафе, Курьер клиента (Яндекс/InDriver).
</delivery_types>

<menu>
{menu_markdown}
</menu>

<order_flow>
ШАГ 1: Размер (1 или 1.5), Лук, Кол-во.
ШАГ 2: Upsell напиток (Кола/Айран) — 1 раз.
ШАГ 3: Способ получения и пожелания.
ШАГ 4: Итог и подтверждение (ДА/НЕТ).
Формат итога:
Ваш заказ:
[Название] — [кол-во] шт (размер X; с луком/без; пожелание: ...) — [цена] тг
Итого: [сумма] тг
Подтвердите: ДА ✅ / НЕТ ❌
ШАГ 5: Если ДА — проси чек Kaspi. БЕЗ чека заказ не создастся.
ШАГ 6: Если видишь "[СИСТЕМА: Клиент прислал чек Kaspi]", СРАЗУ вызывай create_order.
</order_flow>

При жалобах: «Жаль это слышать. Передаю администратору. Номер: +7 777 589 20 72». Вызывай escalate_to_admin."""

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
                                "onion":   {"type": "boolean"},
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

    # Собрать system prompt с актуальным меню
    menu_md = build_menu_markdown()
    
    system_content = SYSTEM_PROMPT_TEMPLATE.format(
        menu_markdown=menu_md,
        kaspi_url=KASPI_PAY_URL,
    )

    messages = [{"role": "system", "content": system_content}] + history

    try:
        response = await _client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=512,
            temperature=0.1,
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
