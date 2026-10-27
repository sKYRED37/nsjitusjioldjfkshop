"""Общее ядро бота. Запускай не его, а bot_pc.py (с компьютера) или bot_host.py (на хостинге)."""
import asyncio
from aiogram.exceptions import TelegramUnauthorizedError
import logging
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)  # bot.db лежит рядом со скриптами (если не задан DB_PATH)

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation  # noqa: E402

import db  # noqa: E402
import links  # noqa: E402
import admin  # noqa: E402
import user  # noqa: E402


def to_ids(value):
    """Достаёт числа-ID из чего угодно: 123, "123", (123,), [1, 2], "1, 2"."""
    if isinstance(value, (list, tuple, set)):
        return [i for v in value for i in to_ids(v)]
    return [int(x) for x in re.findall(r"\d+", str(value or ""))]


def to_token(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip().strip("\"'").strip()


async def main(cfg):
    token = to_token(cfg.get("8944799493:AAF0kSbeUpvgcCp-eULYle3hveuVVTRsQ8A")) or to_token(os.environ.get("BOT_TOKEN"))
    owners = to_ids(cfg.get("OWNER_ID")) or to_ids(os.environ.get("OWNER_ID"))
    if not token or ":" not in token:
        raise SystemExit(
            "\nОткрой bot.py и впиши токен в строку BOT_TOKEN = \"...\" (вверху файла).\n"
            "Токен берётся у @BotFather и выглядит так: 123456789:AAH...")
    if not owners:
        raise SystemExit(
            "\nОткрой bot.py и впиши свой Telegram ID в строку OWNER_ID = ... (вверху файла).\n"
            "Узнать ID можно у бота @userinfobot. Без него в админ-панель никто не попадёт.")
    db.init(owners)
    # Значения из начала файла подставляются, только если в админке ещё ничего не задано
    # (пусто или стоит заглушка по умолчанию). Всё, что ты поменял в панели, остаётся как есть.
    seed = {
        "reviews_chat": cfg.get("REVIEWS_CHAT"),
        "reviews_url": cfg.get("REVIEWS_URL"),
        "channel_url": cfg.get("CHANNEL_URL"),
        "manager": cfg.get("MANAGER"),
        "payment_details": cfg.get("PAYMENT_DETAILS"),
    }
    for key, value in seed.items():
        current = (db.get_setting(key) or "").strip()
        if value and (not current or current == db.DEFAULT_SETTINGS.get(key, "").strip()
                      or (key in ("reviews_url", "channel_url") and not links.norm_url(current))):
            db.set_setting(key, str(value))
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # SimpleEventIsolation: события одного пользователя обрабатываются по очереди —
    # двойное нажатие «Оплата отправлена» не создаст два заказа
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())

    @dp.error()
    async def on_error(event):
        logging.exception("Ошибка в обработчике", exc_info=event.exception)
        upd = event.update
        try:
            if upd.callback_query:
                await upd.callback_query.answer("⚠️ Что-то пошло не так. Попробуй ещё раз.", show_alert=True)
            elif upd.message:
                await upd.message.answer("⚠️ Что-то пошло не так. Попробуй ещё раз или нажми /start.")
        except Exception:
            pass
        return True

    # Роутеры живут на уровне модулей. При авто-перезапуске после сбоя они всё ещё привязаны к прошлому
    # Dispatcher, и include_router падал бы с «Router is already attached» — отвязываем перед подключением.
    for r in (admin.router, user.router):
        r._parent_router = None
    dp.include_router(admin.router)  # админ-роутер первым: он пропускает только админов
    dp.include_router(user.router)
    try:
        try:
            await bot.delete_webhook(drop_pending_updates=False)  # если раньше стоял вебхук — polling иначе не заработает
        except TelegramUnauthorizedError:
            env_tok = to_token(os.environ.get("BOT_TOKEN"))
            source = "переменная окружения BOT_TOKEN на хостинге" if env_tok == token else "token.txt / значение в bot.py"
            raise SystemExit(
                "\n❌ Telegram не принял токен (Unauthorized). Перезапуски не помогут — токен нужно заменить.\n"
                f"   Откуда взят: {source}\n"
                f"   ID бота в токене: {token.split(':')[0]}, длина: {len(token)}, оканчивается на …{token[-4:]}\n"
                "   Возьми актуальный токен в @BotFather (/mybots → бот → API Token) и поменяй его там, откуда он взят.\n")
        await bot.set_my_commands([])  # /admin не показывается в меню
        me = await bot.me()
        print(f"Бот @{me.username} запущен. Админ-панель: /admin")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()  # иначе при перезапуске копятся незакрытые сессии




def run(cfg):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(main(cfg))
    except (KeyboardInterrupt, SystemExit) as e:
        if isinstance(e, SystemExit) and e.code not in (None, 0):
            raise
        print("Бот остановлен.")
