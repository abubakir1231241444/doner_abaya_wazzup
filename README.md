# Донер на Абае — AI-агент 🌯

Полная система автоматизации приёма заказов для заведения общепита.

## Стек
- **FastAPI** — WhatsApp webhook-сервер
- **DeepSeek v3** (через OpenRouter) — AI-агент
- **SendPulse** — отправка сообщений в WhatsApp
- **Supabase** — PostgreSQL база данных
- **aiogram 3** — два Telegram-бота (кассир + курьеры)

## Структура

```
doner_na_abaya/
├── src/           # FastAPI + AI-агент
├── cashier_bot/   # Telegram-бот кассира
├── courier_bot/   # Telegram-бот курьеров
├── db/            # SQL-схема Supabase
└── tests/         # Автотесты
```

## Быстрый старт

### 1. Установка
```bash
cd doner_na_abaya
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 2. Настройка
```bash
copy .env.example .env
# Заполните .env своими ключами
```

### 3. База данных
Откройте `db/schema.sql` и выполните в **Supabase → SQL Editor**.

### 4. Запуск

**Терминал 1 — WhatsApp сервер:**
```bash
uvicorn src.main:app --reload --port 8000
```

**Терминал 2 — Бот кассира:**
```bash
python cashier_bot/main.py
```

**Терминал 3 — Бот курьеров:**
```bash
python courier_bot/main.py
```

**Терминал 4 — Туннель (webhook):**
```bash
cloudflared tunnel --url http://localhost:8000
```
Скопируйте URL и вставьте в SendPulse → Webhook.

### 5. Тесты
```bash
pytest tests/ -v
```

## Ключевые фичи

| Функция | Описание |
|---|---|
| 🤖 AI-диалог | DeepSeek ведёт разговор, понимает рус/каз |
| 📋 Стоп-лист | Кассир переключает наличие позиций прямо в Telegram |
| 💳 Kaspi-чек | Валидация PDF (сумма, дата, получатель) |
| 🛵 Курьеры | Умная маршрутизация — только свободным |
| 💬 Мост ответов | Кассир отвечает в TG → клиент получает в WA |
| 📊 Аналитика | View в Supabase с выручкой по дням |

## Добавить курьера
```sql
INSERT INTO couriers (tg_id, name, status)
VALUES (123456789, 'Имя Курьера', 'offline');
```
