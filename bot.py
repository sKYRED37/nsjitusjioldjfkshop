"""Запуск на хостинге:  python bot.py
Всё настраивается переменными окружения (в панели хостинга), токен в код не вписывается.

  BOT_TOKEN   8944799493:AAF57h-t7wCHk3STi7S240Ic18wdY0FCTTc    обязательно — токен от @BotFather
  OWNER_ID         обязательно — ID админов через запятую: 7434679936,8799415431
  DB_PATH          путь к базе на ПОСТОЯННОМ диске, напр. /data/bot.db (иначе база сотрётся при редеплое)
  REVIEWS_CHAT, REVIEWS_URL, CHANNEL_URL, MANAGER, PAYMENT_DETAILS — необязательно (можно менять в /admin)
"""
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

DEFAULTS = {
    "OWNER_ID": "7434679936,8799415431",
    "REVIEWS_CHAT": "-1003782821891",
    "REVIEWS_URL": "https://t.me/recontrolreviews",
    "CHANNEL_URL": "https://t.me/recontroltags",
    "MANAGER": "@recontrol_tags_manager",
    "PAYMENT_DETAILS": "Карта: 2200 7019 7581 7047\nСБП: +7 966 047 16-44\nБанк: Т-Банк",
}


def env(name):
    v = os.environ.get(name)
    return v.replace("\\n", "\n") if v else DEFAULTS.get(name, "")


def read_token_file():
    """Токен из файла token.txt рядом с bot.py (первая строка, не начинающаяся с #).
    Так не нужно править код: достаточно положить токен в текстовый файл."""
    try:
        with open(os.path.join(HERE, "token.txt"), encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip().strip("\"'")
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return ""


def main():
    import core
    cfg = {k: env(k) for k in ("BOT_TOKEN", "OWNER_ID", "REVIEWS_CHAT", "REVIEWS_URL",
                               "CHANNEL_URL", "MANAGER", "PAYMENT_DETAILS")}
    cfg["BOT_TOKEN"] = cfg["BOT_TOKEN"] or read_token_file()
    if not cfg["BOT_TOKEN"]:
        sys.exit("Токен не найден: впиши его в файл token.txt (рядом с bot.py) или задай переменную BOT_TOKEN.")
    delay = 5
    while True:  # авто-перезапуск при сетевых сбоях
        started = time.time()
        try:
            core.run(cfg)
            return
        except SystemExit:
            raise
        except BaseException:
            logging.exception("Бот упал, перезапуск через %s с", delay)
        delay = 5 if time.time() - started > 120 else min(delay * 2, 60)
        time.sleep(delay)


if __name__ == "__main__":
    main()
