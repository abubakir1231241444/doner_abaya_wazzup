import os
from dotenv import load_dotenv

load_dotenv()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# OpenRouter / DeepSeek
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL   = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v3-0324")

# Webhook Security
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

# Wazzup
WAZZUP_API_KEY    = os.getenv("WAZZUP_API_KEY", "")
WAZZUP_CHANNEL_ID = os.getenv("WAZZUP_CHANNEL_ID", "")

# Telegram — Кассир
CASHIER_BOT_TOKEN = os.getenv("CASHIER_BOT_TOKEN", "")
CASHIER_TG_IDS    = [int(x.strip()) for x in os.getenv("CASHIER_TG_ID", "0").split(",") if x.strip()]

# Kaspi
KASPI_MERCHANT_NAME = os.getenv("KASPI_MERCHANT_NAME", "Souffle Aktobe")
KASPI_MERCHANT_BIN  = os.getenv("KASPI_MERCHANT_BIN", "990424351371")
KASPI_PAY_URL       = os.getenv("KASPI_PAY_URL", "https://pay.kaspi.kz/pay/k7atxtn1")

# Бизнес-логика
TZ               = os.getenv("TZ", "Asia/Oral")
WORK_HOUR_OPEN   = int(os.getenv("WORK_HOUR_OPEN", "10"))
WORK_HOUR_CLOSE  = int(os.getenv("WORK_HOUR_CLOSE", "1"))   # 01:00
ALLOWED_PHONES   = [p.strip() for p in os.getenv("ALLOWED_PHONES", "").split(",") if p.strip()]
BLOCKED_PHONES   = {p.strip() for p in os.getenv("BLOCKED_PHONES", "77081840424").split(",") if p.strip()}
PDF_MAX_AGE_MIN  = 15  # максимальный возраст чека в минутах
