"""Фоновая проверка избранных тегов: скидка, скорое окончание скидки (подорожание), рост цены."""
import asyncio
import html
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import db
from texts import t

CHECK_EVERY = 60  # секунд между проверками
_task = None


def _fmt(n):
    return f"{int(n):,}".replace(",", " ")


def _left(sec):
    if sec is None:
        return "бессрочно"
    d, r = divmod(int(sec), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d} д" + (f" {h} ч" if h else "")
    if h:
        return f"{h} ч" + (f" {m} мин" if m else "")
    return f"{max(m, 1)} мин"


def build(tag, kind, ex):
    name = html.escape(tag["name"])
    base, pct = ex["new"], ex["pct"]
    if kind in ("discount", "ending") and base:
        price = f"от <s>{_fmt(base)}</s> <b>{_fmt(db.apply_discount(base, pct))} ₽</b>" if kind == "discount" \
            else f"от {_fmt(base)} ₽ (сейчас от {_fmt(db.apply_discount(base, pct))} ₽)"
    else:
        price = ""
    if kind == "discount":
        left = "Действует: " + _left(ex["left"])
        return t("txt_fav_n_discount", name=name, pct=pct, price=price, left=left).strip()
    if kind == "ending":
        return t("txt_fav_n_ending", name=name, left=_left(ex["left"]), price=price)
    if kind in ("raised", "lowered"):
        direction = "подорожал" if kind == "raised" else "подешевел"
        return f"{'📈' if kind == 'raised' else '📉'} <b>Тег {name} {direction}</b>\nБыло от {_fmt(ex['old'])} ₽ — стало от {_fmt(ex['new'])} ₽."
    return t("txt_fav_n_ended", name=name, new=_fmt(ex["new"]) if ex["new"] else "—")


def markup(tag_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_fav_open"), callback_data=f"tag:{tag_id}")],
        [InlineKeyboardButton(text=t("btn_fav_list"), callback_data="menu:fav")],
    ])


async def check(bot: Bot):
    for uid, tag, kind, ex in db.fav_events():
        try:
            await bot.send_message(uid, build(tag, kind, ex), reply_markup=markup(tag["id"]))
            db.fav_mark_notified(uid, tag["id"], ex["_state"])
        except Exception:
            logging.exception("Не удалось отправить уведомление избранного uid=%s tag_id=%s", uid, tag["id"])
        await asyncio.sleep(0.05)  # не упираемся в лимиты Telegram


async def _loop(bot: Bot):
    while True:
        try:
            await check(bot)
        except Exception:
            logging.exception("Ошибка проверки избранного")
        await asyncio.sleep(CHECK_EVERY)


def ensure_watcher(bot: Bot):
    """Запускает фоновую проверку один раз, в том числе сразу при старте бота."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(bot), name="favorites-price-watcher")


async def stop_watcher():
    """Останавливает watcher перед закрытием Bot/перезапуском ядра."""
    global _task
    task, _task = _task, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
