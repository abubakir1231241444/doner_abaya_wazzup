"""
Тесты для menu_builder.py — с mock-данными из БД.
"""
from unittest.mock import patch
from src.menu_builder import build_menu_markdown

MOCK_MENU = [
    {"id": 1, "category": "Основное меню", "name": "Куриный донер размер-1",   "price": 1895, "is_available": True,  "sort_order": 1},
    {"id": 2, "category": "Основное меню", "name": "Говяжий донер размер-1",   "price": 1995, "is_available": False, "sort_order": 2},
    {"id": 3, "category": "Напитки",       "name": "Coca Cola 0.5л",           "price": 695,  "is_available": True,  "sort_order": 1},
    {"id": 4, "category": "Ассортимент",   "name": "Красный соус",             "price": 150,  "is_available": True,  "sort_order": 1},
]


def test_menu_contains_categories():
    with patch("src.menu_builder.get_menu", return_value=MOCK_MENU):
        md = build_menu_markdown()
    assert "### Основное меню" in md
    assert "### Напитки" in md
    assert "### Ассортимент" in md


def test_available_status():
    with patch("src.menu_builder.get_menu", return_value=MOCK_MENU):
        md = build_menu_markdown()
    assert "В наличии" in md
    assert "Нет в наличии" in md


def test_prices_in_output():
    with patch("src.menu_builder.get_menu", return_value=MOCK_MENU):
        md = build_menu_markdown()
    assert "1895 тг" in md
    assert "695 тг" in md


def test_empty_menu():
    with patch("src.menu_builder.get_menu", return_value=[]):
        md = build_menu_markdown()
    assert "недоступно" in md.lower()
