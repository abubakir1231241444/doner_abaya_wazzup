# Донер на Абае — AI-агент 🌯

Полная система автоматизации приёма заказов для заведения общепита.

## Стек
- **FastAPI** — WhatsApp webhook-сервер
- **DeepSeek v3** (через OpenRouter) — AI-агент
- **Wazzup** — отправка/приём сообщений WhatsApp (API v3)
- **Supabase** — PostgreSQL база данных
- **aiogram 3** — Telegram-бот кассира

## Структура

```
doner_abaya_wazzup/
├── src/           # FastAPI + AI-агент + Wazzup API
├── cashier_bot/   # Telegram-бот кассира
├── scripts/       # Утилиты (регистрация вебхука и пр.)
├── db/            # SQL-схема Supabase
└── tests/         # Автотесты
```

## Быстрый старт

### 1. Установка
```bash
cd doner_abaya_wazzup
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 2. Настройка
```bash
copy .env.example .env
# Заполните .env своими ключами (Wazzup API Key, Channel ID и т.д.)
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

**Терминал 3 — Туннель (webhook):**
```bash
cloudflared tunnel --url http://localhost:8000
```

### 5. Регистрация вебхука Wazzup
После запуска туннеля скопируйте URL и зарегистрируйте:
```bash
python scripts/register_webhook.py https://YOUR-TUNNEL.trycloudflare.com
```

Или через API (сервер должен быть запущен):
```bash
curl -X POST http://localhost:8000/internal/register-webhook \
  -H "Content-Type: application/json" \
  -d '{"url": "https://YOUR-TUNNEL.trycloudflare.com/webhook"}'
```

### 6. Тесты
```bash
pytest tests/ -v
```

## Ключевые фичи

| Функция | Описание |
|---|---|
| 🤖 AI-диалог | DeepSeek ведёт разговор, понимает рус/каз |
| 📋 Стоп-лист | Кассир переключает наличие позиций прямо в Telegram |
| 💳 Kaspi-чек | Валидация PDF (сумма, дата, получатель) |
| 💬 Мост ответов | Кассир отвечает в TG → клиент получает в WA |
| 📊 Аналитика | View в Supabase с выручкой по дням |

## API Endpoints

| Метод | URL | Описание |
|---|---|---|
| GET | `/health` | Проверка состояния |
| POST | `/webhook` | Вебхук Wazzup (входящие сообщения) |
| POST | `/internal/resume/{phone}` | Снять паузу с клиента |
| POST | `/internal/register-webhook` | Зарегистрировать URL вебхука |
| GET | `/internal/webhook-info` | Текущий зарегистрированный вебхук |
