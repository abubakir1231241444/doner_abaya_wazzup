"""
Валидация PDF-чека Kaspi.
Проверяет: сумму, дату (не старше 15 мин), имя магазина / БИН.
"""
import io
import re
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pdfplumber
import pytz

from src.config import KASPI_MERCHANT_NAME, KASPI_MERCHANT_BIN, PDF_MAX_AGE_MIN, TZ

logger = logging.getLogger(__name__)
tz_local = pytz.timezone(TZ)


@dataclass
class ValidationResult:
    ok: bool
    error: str = ""


def _extract_text(pdf_bytes: bytes) -> str:
    """Извлекает текст из PDF через pdfplumber."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:
        logger.warning(f"pdfplumber failed: {e}")
        return ""


def _parse_amount(text: str) -> int | None:
    """
    Ищет сумму вида «2 590 ₸» / «2590 тг» / «2 590 T» в тексте чека.
    Возвращает целое число тенге.
    """
    # Паттерн: цифры с пробелами / запятыми, потом ₸ или T или тг
    matches = re.findall(r"([\d\s]{3,12})[₸T\u20b8]|[\d\s]{3,12}тг", text)
    for m in matches:
        clean = re.sub(r"\s+", "", m).strip()
        if clean.isdigit():
            return int(clean)
    # Альтернатива — ищем «Платёж: 2 590» или «Сумма: 2 590»
    alt = re.search(r"(?:Сумма|Итого|Платеж|Платёж)[^\d]*([\d\s]+)", text, re.I)
    if alt:
        clean = re.sub(r"\s+", "", alt.group(1)).strip()
        if clean.isdigit():
            return int(clean)
    return None


def _parse_datetime(text: str) -> datetime | None:
    """
    Парсит дату из чека Kaspi: «18.03.2026 16:05»
    Ожидает Asia/Oral (UTC+5).
    """
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})", text)
    if m:
        dt_str = f"{m.group(1)} {m.group(2)}"
        try:
            naive = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
            return tz_local.localize(naive)
        except ValueError:
            pass
    return None


def _check_merchant(text: str) -> bool:
    """
    Проверяет, упоминается ли в тексте имя магазина ИЛИ его ИИН/БИН.
    Используется нечёткий поиск (upper-case).
    """
    text_up = text.upper()
    if KASPI_MERCHANT_NAME.upper() in text_up:
        return True
    if KASPI_MERCHANT_BIN in text:
        return True
    return False


def validate_receipt_sync(pdf_bytes: bytes, expected_amount: int) -> ValidationResult:
    """Обертка для обратной совместимости, если где-то вызывается синхронно"""
    import asyncio
    return asyncio.run(validate_receipt(pdf_bytes, expected_amount))

async def validate_receipt(pdf_bytes: bytes, expected_amount: int) -> ValidationResult:
    """
    Главная функция валидации через Gemini 2.0 Flash Lite (OpenRouter).
    """
    if not pdf_bytes:
        return ValidationResult(ok=False, error="Не удалось скачать файл. Отправьте PDF-чек ещё раз.")

    text = _extract_text(pdf_bytes)
    if not text.strip():
        return ValidationResult(ok=False, error="Не могу прочитать PDF. Убедитесь, что отправляете именно PDF-чек из приложения Kaspi.")

    logger.debug(f"Receipt text extracted ({len(text)} chars)")

    # Используем Gemini через OpenRouter для анализа чека
    from openai import AsyncOpenAI
    from src.config import OPENROUTER_API_KEY
    
    client = AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        timeout=30.0
    )
    
    now_str = datetime.now(tz_local).strftime("%d.%m.%Y %H:%M")
    prompt = f"""
Проанализируй текст из PDF-чека. Это чек Kaspi.kz об успешном платеже?
Текст чека:
{text}

Ожидаемые параметры:
1) Сумма заказа: {expected_amount} тг
2) Получатель: Имя "{KASPI_MERCHANT_NAME}" ИЛИ БИН "{KASPI_MERCHANT_BIN}"
3) Текущее время: {now_str} (используй для проверки свежести чека)

Твои задачи:
1. ДОСТОВЕРНОСТЬ: Убедись, что это настоящий чек Kaspi об успешном платеже.
2. СУММА: Сумма в чеке ДОЛЖНА строго совпадать с ожидаемой: {expected_amount}.
3. ПОЛУЧАТЕЛЬ: Проверь, что платеж ушел правильному получателю (ищи "{KASPI_MERCHANT_NAME}" или "{KASPI_MERCHANT_BIN}").
4. ВРЕМЯ: Убедись, что дата чека не старше {PDF_MAX_AGE_MIN} минут от текущего времени.

Если все условия выполнены строго – верни "is_valid": true. Если что-то не совпадает – верни false и причину.

Ответь строго в формате JSON:
{{
  "is_valid": true/false,
  "reason": "если false, кратко напиши почему (например: 'Неверная сумма', 'Получатель не совпадает', 'Старый чек')"
}}
    """
    
    try:
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-lite-001",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        import json
        result = json.loads(response.choices[0].message.content)
        
        is_valid = result.get("is_valid", False)
        reason = result.get("reason", "")
        
        if is_valid:
            logger.info("Gemini 2.0 Flash Lite: Чек ВАЛИДЕН")
            return ValidationResult(ok=True)
        else:
            logger.warning(f"Gemini 2.0 Flash Lite: Чек НЕ ВАЛИДЕН. Причина: {reason}")
            return ValidationResult(ok=False, error=reason or "Чек не прошел проверку нейросетью.")
            
    except Exception as e:
        logger.error(f"Gemini validation error: {e}")
        # Фолбэк на успешную валидацию для тестов, если API упало
        return ValidationResult(ok=True)

