"""
Скрипт для регистрации вебхука в Wazzup.

Использование:
  python scripts/register_webhook.py https://your-tunnel.trycloudflare.com/webhook
  
Wazzup пришлёт тестовый POST { "test": true } на этот URL — 
сервер (main.py) должен быть запущен и ответить 200.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.wazzup import register_webhook, get_webhook_info


async def main():
    if len(sys.argv) < 2:
        # Без аргумента — показать текущий вебхук
        print("Текущий вебхук:")
        info = await get_webhook_info()
        print(info)
        print("\nЧтобы зарегистрировать: python scripts/register_webhook.py <URL>")
        return

    url = sys.argv[1].rstrip("/")
    
    # Добавляем /webhook если не указан
    if not url.endswith("/webhook"):
        url = url + "/webhook"
    
    print(f"Регистрирую вебхук: {url}")
    ok = await register_webhook(url)
    
    if ok:
        print("✅ Вебхук зарегистрирован!")
    else:
        print("❌ Ошибка регистрации вебхука")
    
    # Проверяем результат
    print("\nТекущие настройки:")
    info = await get_webhook_info()
    print(info)


if __name__ == "__main__":
    asyncio.run(main())
