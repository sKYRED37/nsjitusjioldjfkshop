# Бот для хостинга (bothost)
Файл запуска: `bot.py`, команда: `python bot.py`, зависимости: `requirements.txt`.
Переменные окружения:
- `BOT_TOKEN` — токен от @BotFather (обязательно)
- `OWNER_ID` — ID админов через запятую (по умолчанию уже заданы)
- `DB_PATH` — путь к базе на постоянном диске, напр. `/data/bot.db` (иначе база сотрётся при редеплое)
- `MANAGER`, `PAYMENT_DETAILS`, `CHANNEL_URL`, `REVIEWS_URL`, `REVIEWS_CHAT` — по желанию, меняются и в /admin.
Админ-панель: `/admin`. Бот сам перезапускается при сбоях.
