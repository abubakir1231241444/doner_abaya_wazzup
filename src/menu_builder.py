"""
Генерация Markdown-таблицы меню из Supabase для подстановки в системный промпт.
Включает логику связи: ассорти недоступно если нет курицы или говядины.
"""
from src.db import get_menu, get_db


def _apply_linked_availability(items: list[dict]) -> list[dict]:
    """
    Ассорти зависит от наличия Курицы и Говядины.
    Если хотя бы одного из них нет — ассорти автоматически недоступно.
    """
    # Проверяем наличие курицы и говядины (хотя бы один размер)
    has_chicken = any(
        "куриный" in item["name"].lower() and item["is_available"]
        for item in items
    )
    has_beef = any(
        "говяжий" in item["name"].lower() and item["is_available"]
        for item in items
    )

    for item in items:
        if "ассорти" in item["name"].lower():
            if not has_chicken or not has_beef:
                item["is_available"] = False
                item["_auto_blocked"] = True  # маркер для UI

    return items


def build_menu_markdown() -> str:
    """
    Возвращает Markdown-таблицы по категориям.
    Пример:
        ### Основное меню
        | Позиция | Цена | Статус |
        |---|---|---|
        | Куриный донер размер-1 | 1895 тг | В наличии |
    """
    items = get_menu()
    if not items:
        return "*Меню временно недоступно*"

    # Применяем логику связи ассорти
    items = _apply_linked_availability(items)

    # Группируем по категории
    categories: dict[str, list] = {}
    for item in items:
        cat = item["category"]
        categories.setdefault(cat, []).append(item)

    lines = []
    # Фиксированный порядок категорий
    category_order = ["Основное меню", "Напитки", "Ассортимент"]
    for cat in category_order:
        if cat not in categories:
            continue
        lines.append(f"\n### {cat}")
        lines.append("| Позиция | Цена | Статус |")
        lines.append("|---|---|---|")
        for item in categories[cat]:
            status = "В наличии" if item["is_available"] else "Нет в наличии"
            lines.append(f"| {item['name']} | {item['price']} тг | {status} |")

    return "\n".join(lines)


def get_stoplist_grouped() -> list[dict]:
    """
    Возвращает упрощённый стоп-лист для Telegram-бота кассира.
    Группирует размеры: "Куриный донер" (все размеры) → один пункт.
    """
    items = get_menu()
    items = _apply_linked_availability(items)

    # Группируем "Основное меню" по ключевому слову
    groups = {}
    extras = []  # всё что не основное меню

    for item in items:
        if item["category"] == "Основное меню":
            # Определяем группу: Куриный / Говяжий / Ассорти
            name_lower = item["name"].lower()
            if "куриный" in name_lower:
                key = "🐔 Куриный донер"
            elif "говяжий" in name_lower:
                key = "🐄 Говяжий донер"
            elif "ассорти" in name_lower:
                key = "🥙 Ассорти донер"
            else:
                key = item["name"]

            if key not in groups:
                groups[key] = {
                    "label": key,
                    "ids": [],
                    "is_available": True,
                    "auto_blocked": False
                }
            groups[key]["ids"].append(item["id"])
            # Группа доступна если ВСЕ её размеры доступны
            if not item["is_available"]:
                groups[key]["is_available"] = False
            if item.get("_auto_blocked"):
                groups[key]["auto_blocked"] = True
        else:
            extras.append({
                "label": f"{item['name']}",
                "ids": [item["id"]],
                "is_available": item["is_available"],
                "auto_blocked": False,
                "category": item["category"]
            })

    result = list(groups.values()) + extras
    return result
