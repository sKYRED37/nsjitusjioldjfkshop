import asyncio
import hashlib
import logging
import html
import re
import time

from aiogram import Router, F, Bot, BaseMiddleware
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, BufferedInputFile

import banner
import db
import favorites
import links
from texts import t

router = Router()
PAGE = db.USER_PAGE


class Order(StatesGroup):
    game_id = State()
    promo = State()
    proof = State()


class Ask(StatesGroup):
    question = State()


class TagSearch(StatesGroup):
    query = State()


class CasePayment(StatesGroup):
    proof = State()


class CaseIssue(StatesGroup):
    game_id = State()


def esc(s):
    return html.escape(str(s))


def fmt(n):
    return f"{n:,}".replace(",", " ")


def manager_handle():
    raw = db.get_setting("manager")
    return links.norm_handle(raw) or links.clean(raw)


def mgr():
    return esc(manager_handle())


def manager_url():
    return links.tme(links.norm_handle(db.get_setting("manager")))


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Цвет кнопок: по умолчанию эмодзи 🟢 (скидка) и 🔴 (хит в админке) — работает везде.
USE_NATIVE_STYLE = True  # Используем нативные цвета кнопок, если установленный aiogram их поддерживает.
STYLE_OK = USE_NATIVE_STYLE and "style" in getattr(InlineKeyboardButton, "model_fields", {})
_FALLBACK_EMOJI = {"success": "🟢 ", "danger": "🔴 "}


def btn(text, data=None, url=None, style=None):
    if style and STYLE_OK:
        return InlineKeyboardButton(text=text, callback_data=data, url=url, style=style)
    if style:
        text = _FALLBACK_EMOJI.get(style, "") + text
    return InlineKeyboardButton(text=text, callback_data=data, url=url)


def tag_label(tg):
    """Подпись тега: хит отмечается огоньком, скидка — зелёным маркером и полным текстом."""
    pct = db.disc_of(tg)
    name = tg["name"] + (f" - Скидка {pct}%" if pct else "")
    return ("🔥 " if tg["hit"] else "") + (("🟢 " + name) if pct else name)


def tag_btn(tg, data):
    """Кнопка тега: хит — красный стиль, скидка — зелёный стиль; закрытый — с замком."""
    # Если одновременно хит и скидка, приоритет красного стиля у хита,
    # а скидка дополнительно отмечается зелёным маркером в подписи.
    style = "danger" if tg["hit"] else ("success" if db.disc_of(tg) else None)
    pct = db.disc_of(tg)
    # Красный маркер кнопки уже обозначает хит; не дублируем его вторым огоньком.
    label = (("🟢 " if pct else "") + tg["name"] + (f" - Скидка {pct}%" if pct else ""))
    return btn(("🔒 " if tg["closed"] else "") + label, data, style=style)


def left_str(sec):
    """Сколько осталось: «2 д 3 ч», «5 ч 10 мин»."""
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


_banner_ids = {}  # кэш file_id сгенерированных баннеров: ключ → file_id


async def show(target, text, markup=None, banner=None, fid=None, gen=None):
    """Показывает экран. Если для экрана загружен баннер — сообщение идёт с картинкой.
    gen = (ключ, байты_картинки) — сгенерированный баннер; после первой отправки кэшируется file_id."""
    gen_key = None
    if gen and not fid:
        gen_key = gen[0]
        fid = _banner_ids.get(gen_key) or BufferedInputFile(gen[1], filename="banner.jpg")
    elif not fid and banner:
        fid = db.get_setting("banner_" + banner)
    if fid and len(text) > 1000:
        fid = ""  # подпись к фото ограничена 1024 символами — длинный экран показываем без баннера
        gen_key = None
    res = None
    if isinstance(target, CallbackQuery):
        msg = target.message
        try:
            if fid:
                if msg.photo:
                    res = await msg.edit_media(InputMediaPhoto(media=fid, caption=text), reply_markup=markup)
                else:
                    await msg.delete()
                    res = await msg.answer_photo(fid, caption=text, reply_markup=markup)
            elif msg.photo:
                await msg.delete()
                await msg.answer(text, reply_markup=markup)
            else:
                await msg.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                if fid:
                    res = await msg.answer_photo(fid, caption=text, reply_markup=markup)
                else:
                    await msg.answer(text, reply_markup=markup)
        await target.answer()
    elif fid:
        res = await target.answer_photo(fid, caption=text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)
    if gen_key and res is not None and getattr(res, "photo", None):
        _banner_ids[gen_key] = res.photo[-1].file_id


# ---------------- главное меню ----------------
def reviews_link():
    url = links.norm_url(db.get_setting("reviews_url"))
    if url:
        return url
    chat = links.norm_chat(db.get_setting("reviews_chat") or "")
    if chat and chat.startswith("@"):
        return links.tme(chat)
    return None


def main_menu():
    g = t
    rv = reviews_link()
    row3 = ([btn(g("btn_reviews"), url=rv)] if rv else []) + [btn(g("btn_help"), "menu:help")]
    return kb([
        [btn(g("btn_catalog"), "menu:catalog")],
        [btn("🔎 Умный поиск тегов", "search:open"), btn("🎁 Кейсы", "cases:list")],
        [btn(g("btn_coins"), "menu:coins"), btn(g("btn_bonus"), "menu:bonus")],
        [btn(g("btn_promo"), "menu:promo"), btn(g("btn_orders"), "menu:orders")],
        [btn(g("btn_fav"), "menu:fav")],
        row3,
        [btn(g("btn_rules"), "menu:rules")],
    ])


def back_menu():
    return kb([[btn(t("btn_menu"), "menu:main")]])


@router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject = None):
    await state.clear()
    is_new = db.one("SELECT 1 FROM users WHERE user_id=?", (m.from_user.id,)) is None
    db.ex("INSERT OR IGNORE INTO users(user_id,username,joined_at) VALUES(?,?,?)", (m.from_user.id, m.from_user.username, int(time.time())))
    # реферальная ссылка: t.me/бот?start=ref_КОД — считаем каждого человека один раз
    arg = (command.args or "").strip() if command else ""
    if arg.startswith("ref_") and not db.is_admin(m.from_user.id):  # свои переходы админов не считаем
        code = arg[4:]
        if db.one("SELECT 1 FROM refs WHERE code=?", (code,)):
            db.ex("INSERT OR IGNORE INTO ref_visits(code,user_id,is_new,at) VALUES(?,?,?,?)",
                  (code, m.from_user.id, 1 if is_new else 0, int(time.time())))
    await show(m, t("welcome"), main_menu(), "main")


def esc_keep(s):
    return s  # тексты настроек админ пишет сам (допустим HTML)


@router.callback_query(F.data == "menu:main")
async def cb_main(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, t("welcome"), main_menu(), "main")


def help_rows():
    rows = []
    ch = links.norm_url(db.get_setting("channel_url"))
    if ch:
        rows.append([btn(t("btn_channel"), url=ch)])
    rows.append([btn(t("btn_ask"), "help:ask")])
    if manager_url():
        rows.append([btn(t("btn_manager", manager=manager_handle()), url=manager_url())])
    rows.append([btn(t("btn_menu"), "menu:main")])
    return rows


@router.callback_query(F.data == "help:ask")
async def cb_ask(c: CallbackQuery, state: FSMContext):
    await state.set_state(Ask.question)
    await show(c, t("txt_ask"),
               kb([[btn(t("btn_back"), "menu:help")]]))


@router.message(Ask.question)
async def got_question(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    uname = f"@{m.from_user.username}" if m.from_user.username else f"id{m.from_user.id}"
    text = f"❓ <b>Вопрос от {esc(uname)}</b> (<code>{m.from_user.id}</code>)\n\n{esc(m.text or '[не текст]')}"
    markup = kb([[btn("💬 Ответить", f"adm:reply:{m.from_user.id}")]])
    for aid in db.admin_ids():
        try:
            await bot.send_message(aid, text, reply_markup=markup)
        except Exception:
            pass
    await m.answer(t("txt_ask_sent"), reply_markup=back_menu())


@router.callback_query(F.data == "menu:help")
async def cb_help(c: CallbackQuery, state: FSMContext):
    await state.clear()
    text = t("help", manager=mgr())
    await show(c, text, kb(help_rows()), "help")


@router.callback_query(F.data == "menu:rules")
async def cb_rules(c: CallbackQuery, state: FSMContext):
    await state.clear()
    text = t("rules", manager=mgr())
    await show(c, text, back_menu(), "rules")


@router.callback_query(F.data == "menu:orders")
async def cb_orders(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = db.q("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (c.from_user.id,))
    if not rows:
        text = t("txt_orders_title") + "\n\n" + t("txt_orders_empty")
        markup = kb([[btn(t("btn_to_catalog"), "menu:catalog")], [btn(t("btn_menu"), "menu:main")]])
    else:
        st = {"pending": t("txt_st_pending"), "paid": t("txt_st_paid"), "rejected": t("txt_st_rejected")}
        lines = [t("txt_orders_title") + "\n"]
        for o in rows:
            lines.append(
                f"#{o['id']} · {esc(o['tag'])} · {esc(o['term'])} · {db.money(o['amount'], o['method'])}"
                f" — {st.get(o['status'], o['status'])}"
            )
        text = "\n".join(lines)
        rv = [btn(t("btn_review_order", id=o["id"]), f"rv:start:{o['id']}") for o in rows
              if o["status"] == "paid" and not o["reviewed"]][:3] if db.get_setting("reviews_chat") else []
        markup = kb([[b] for b in rv] + [[btn(t("btn_menu"), "menu:main")]])
    await show(c, text, markup, "orders")



# ---------------- умный поиск ----------------
@router.callback_query(F.data == "search:open")
async def search_open(c: CallbackQuery, state: FSMContext):
    await state.set_state(TagSearch.query)
    await show(c, "🔎 <b>Умный поиск тегов</b>\n\nВведи параметры через точку с запятой:\n<code>название;мин.цена;макс.цена;мин.длина;макс.длина;скидка;хит</code>\n\nПример: <code>dragon;100;1000;3;12;1;0</code>\nПустое поле означает «любой». Для скидки и хита: 1 — да, 0 — нет.", back_menu())


@router.message(TagSearch.query)
async def search_run(m: Message, state: FSMContext):
    raw = (m.text or "").strip().split(";")
    raw += [""] * (7-len(raw))
    name, lo, hi, lmin, lmax, discount, hit = [x.strip() for x in raw[:7]]
    try:
        sql = """SELECT t.*, MIN(p.price_rub) AS min_price
                 FROM tags t LEFT JOIN prices p ON p.tag_id=t.id
                 WHERE t.hidden=0 AND t.closed=0"""
        args = []
        if name:
            sql += " AND lower(t.name) LIKE ?"
            args.append("%" + name.casefold() + "%")
        if lmin:
            if int(lmin) < 0: raise ValueError()
            sql += " AND length(t.name) >= ?"; args.append(int(lmin))
        if lmax:
            if int(lmax) < 0: raise ValueError()
            sql += " AND length(t.name) <= ?"; args.append(int(lmax))
        if discount not in ("", "0", "1") or hit not in ("", "0", "1"):
            raise ValueError()
        sql += " GROUP BY t.id HAVING 1=1"
        if lo:
            if int(lo) < 0: raise ValueError()
            sql += " AND MIN(p.price_rub) >= ?"; args.append(int(lo))
        if hi:
            if int(hi) < 0: raise ValueError()
            sql += " AND MIN(p.price_rub) <= ?"; args.append(int(hi))
        if discount == "1":
            sql += " AND COALESCE(t.disc_pct,0)>0 AND (t.disc_until IS NULL OR t.disc_until>?)"
            args.append(int(time.time()))
        elif discount == "0":
            sql += " AND (COALESCE(t.disc_pct,0)=0 OR (t.disc_until IS NOT NULL AND t.disc_until<=?))"
            args.append(int(time.time()))
        if hit in ("0", "1"):
            sql += " AND COALESCE(t.hit,0)=?"
            args.append(int(hit))
        sql += " ORDER BY COALESCE(t.hit,0) DESC, t.id DESC LIMIT 30"
        rows = db.q(sql, tuple(args))
    except (ValueError, TypeError):
        await state.clear()
        return await m.answer("Не удалось разобрать фильтры. Используй числа для цен и длины, а для скидки/хита — 1 или 0.", reply_markup=back_menu())
    await state.clear()
    buttons = [[tag_btn(tg, f"tag:{tg['id']}")] for tg in rows]
    buttons += [[btn("🔎 Изменить фильтры", "search:open")], [btn(t("btn_menu"), "menu:main")]]
    await m.answer(f"Найдено тегов: {len(rows)}" if rows else "По заданным фильтрам ничего не найдено.", reply_markup=kb(buttons))


# ---------------- кейсы: оплата скриншотом и подтверждение владельцем ----------------
@router.callback_query(F.data == "cases:list")
async def cases_list(c: CallbackQuery):
    cases = db.q("SELECT * FROM cases WHERE active=1 ORDER BY id")
    rows = [[btn(f"🎁 {x['name']} · {fmt(x['price_rub'])} ₽ / {fmt(x['price_gold'])} голды", f"cases:buy:{x['id']}")] for x in cases]
    rows += [[btn("🎒 Мои кейсы", "cases:mine")], [btn(t("btn_menu"), "menu:main")]]
    await show(c, "🎁 <b>Кейсы с тегами</b>\nВыбери кейс и способ оплаты. После проверки скриншота владельцем тег будет выдан автоматически.", kb(rows))


@router.callback_query(F.data == "cases:mine")
async def cases_mine(c: CallbackQuery):
    rows = db.q("SELECT d.*, c.name case_name, t.name tag_name FROM case_draws d JOIN cases c ON c.id=d.case_id JOIN tags t ON t.id=d.tag_id WHERE d.user_id=? ORDER BY d.id DESC LIMIT 50", (c.from_user.id,))
    lines, buttons = [], []
    statuses = {
        "not_requested": "🟡 Ожидает заявки на выдачу",
        "pending": "⏳ Заявка отправлена владельцу",
        "issued": "✅ Тег выдан",
        "refused": "❌ В выдаче отказано",
    }
    for x in rows:
        lines.append(f"🎁 <b>{esc(x['case_name'])}</b> → <b>{esc(x['tag_name'])}</b>\n{statuses.get(x['issue_status'] or 'not_requested', 'Статус уточняется')}")
        if (x["issue_status"] or "not_requested") in ("not_requested", "refused"):
            buttons.append([btn("📨 Подать заявку на выдачу · " + esc(x["tag_name"]), f"cases:issue:{x['id']}")])
    text = "🎒 <b>Мои кейсы</b>\nЗдесь находятся открытые кейсы и выпавшие теги. Чтобы получить тег в игре, подай заявку на выдачу.\n\n" + ("\n\n".join(lines) if lines else "Пока нет открытых кейсов.")
    buttons += [[btn("🎁 К кейсам", "cases:list")], [btn(t("btn_menu"), "menu:main")]]
    await show(c, text, kb(buttons))


@router.callback_query(F.data.regexp(r"^cases:issue:\d+$"))
async def case_issue_start(c: CallbackQuery, state: FSMContext):
    draw_id = int(c.data.split(":")[2])
    draw = db.one("SELECT * FROM case_draws WHERE id=? AND user_id=?", (draw_id, c.from_user.id))
    if not draw:
        return await c.answer("Кейс не найден", show_alert=True)
    if (draw["issue_status"] or "not_requested") not in ("not_requested", "refused"):
        return await c.answer("Заявка уже отправлена или тег уже выдан", show_alert=True)
    await state.update_data(case_draw_id=draw_id)
    await state.set_state(CaseIssue.game_id)
    await show(c, "🆔 <b>Заявка на выдачу тега</b>\n\nОтправь свой игровой ID, на который владелец должен выдать тег:", kb([[btn("↩️ Отмена", "cases:mine")]]))


@router.message(CaseIssue.game_id)
async def case_issue_submit(m: Message, state: FSMContext, bot: Bot):
    game_id = (m.text or "").strip()
    if not re.fullmatch(r"\d{5,20}", game_id):
        return await m.answer("⚠️ ID должен содержать от 5 до 20 цифр. Отправь игровой ID ещё раз.")
    data = await state.get_data()
    draw_id = data.get("case_draw_id")
    draw = db.one("SELECT d.*, c.name case_name, t.name tag_name FROM case_draws d JOIN cases c ON c.id=d.case_id JOIN tags t ON t.id=d.tag_id WHERE d.id=? AND d.user_id=?", (draw_id, m.from_user.id))
    if not draw or (draw["issue_status"] or "not_requested") not in ("not_requested", "refused"):
        await state.clear()
        return await m.answer("Этот кейс уже нельзя отправить на выдачу.", reply_markup=kb([[btn("🎒 Мои кейсы", "cases:mine")]]))
    db.ex("UPDATE case_draws SET game_id=?, issue_status='pending', requested_at=?, decided_at=NULL WHERE id=? AND user_id=?", (game_id, int(time.time()), draw_id, m.from_user.id))
    await state.clear()
    await m.answer("✅ Заявка отправлена владельцу. Когда тег будет выдан или в выдаче будет отказано, я сообщу тебе.", reply_markup=kb([[btn("🎒 Мои кейсы", "cases:mine")]]))
    username = f"@{m.from_user.username}" if m.from_user.username else "без username"
    msg = (f"📨 <b>Заявка на выдачу тега из кейса</b>\n"
           f"Покупатель: {esc(username)}\nTelegram ID: <code>{m.from_user.id}</code>\n"
           f"Игровой ID: <code>{esc(game_id)}</code>\nКейс: <b>{esc(draw['case_name'])}</b>\n"
           f"Тег: <b>{esc(draw['tag_name'])}</b>\nID открытия: <code>{draw_id}</code>")
    markup = kb([[btn("✅ Выдан", f"adm:caseissue:issued:{draw_id}"), btn("❌ Отказан", f"adm:caseissue:refused:{draw_id}")]])
    for aid in db.admin_ids():
        try:
            await bot.send_message(aid, msg, reply_markup=markup)
        except Exception:
            pass


@router.callback_query(F.data.regexp(r"^cases:buy:\d+$"))
async def case_buy(c: CallbackQuery):
    cid = int(c.data.split(":")[2])
    case = db.one("SELECT * FROM cases WHERE id=? AND active=1", (cid,))
    if not case:
        return await c.answer("Кейс недоступен", show_alert=True)
    pool = db.q("SELECT 1 FROM case_tags ct JOIN tags t ON t.id=ct.tag_id WHERE ct.case_id=? AND t.hidden=0 AND t.closed=0", (cid,))
    if not pool:
        return await c.answer("В кейсе пока нет тегов", show_alert=True)
    rows = []
    if int(case["price_rub"] or 0) > 0:
        rows.append([btn(f"💳 Оплатить {fmt(case['price_rub'])} ₽", f"cases:pay:{cid}:card")])
    if int(case["price_gold"] or 0) > 0:
        rows.append([btn(f"🪙 Оплатить {fmt(case['price_gold'])} голды", f"cases:pay:{cid}:gold")])
    rows.append([btn("↩️ Назад", "cases:list")])
    await show(c, f"🎁 <b>{esc(case['name'])}</b> · {esc(case['rarity'])}\nВыбери способ оплаты:", kb(rows))


@router.callback_query(F.data.regexp(r"^cases:pay:\d+:(card|gold)$"))
async def case_pay(c: CallbackQuery, state: FSMContext):
    _, _, cid, method = c.data.split(":")
    case = db.one("SELECT * FROM cases WHERE id=? AND active=1", (int(cid),))
    if not case:
        return await c.answer("Кейс недоступен", show_alert=True)
    amount = int(case["price_rub"] if method == "card" else case["price_gold"] or 0)
    if amount <= 0:
        return await c.answer("Этот способ оплаты не настроен", show_alert=True)
    await state.update_data(case_id=int(cid), case_method=method, case_amount=amount)
    await state.set_state(CasePayment.proof)
    details = db.get_setting("payment_details") if method == "card" else "Переведи указанное количество голды владельцу/по согласованным реквизитам."
    await show(c, f"Оплати <b>{fmt(amount)} {'₽' if method == 'card' else 'голды'}</b>.\n\n{esc(details)}\n\nЗатем отправь сюда скриншот оплаты.", kb([[btn("↩️ Отмена", "cases:list")]]))


@router.message(CasePayment.proof)
async def case_payment_proof(m: Message, state: FSMContext, bot: Bot):
    file_id = m.photo[-1].file_id if m.photo else (m.document.file_id if m.document and (m.document.mime_type or "").startswith("image/") else None)
    if not file_id:
        return await m.answer("Пришли скриншот оплаты фотографией или изображением-документом.")
    data = await state.get_data()
    case = db.one("SELECT * FROM cases WHERE id=? AND active=1", (data.get("case_id"),))
    if not case:
        await state.clear(); return await m.answer("Кейс больше недоступен.")
    oid = db.ex("INSERT INTO case_orders(user_id,username,case_id,method,amount,proof,created_at) VALUES(?,?,?,?,?,?,?)", (m.from_user.id, m.from_user.username, case["id"], data["case_method"], data["case_amount"], file_id, int(time.time())))
    await state.clear()
    await m.answer(f"✅ Заказ #{oid} отправлен владельцу на проверку. После подтверждения тег автоматически появится в разделе «Мои кейсы».", reply_markup=kb([[btn("🎒 Мои кейсы", "cases:mine")], [btn("🎁 К кейсам", "cases:list")]]))
    uname = f"@{m.from_user.username}" if m.from_user.username else str(m.from_user.id)
    caption = f"🎁 <b>Оплата кейса #{oid}</b>\nКейс: {esc(case['name'])} ({esc(case['rarity'])})\nПокупатель: {esc(uname)} / <code>{m.from_user.id}</code>\nСумма: {fmt(data['case_amount'])} {'₽' if data['case_method']=='card' else 'голды'}"
    markup = kb([[btn("✅ Подтвердить и выдать тег", f"adm:caseok:{oid}"), btn("❌ Отклонить", f"adm:caseno:{oid}")]])
    for aid in db.admin_ids():
        try: await bot.send_photo(aid, file_id, caption=caption, reply_markup=markup)
        except Exception: pass


# ---------------- каталог ----------------
@router.callback_query(F.data == "menu:catalog")
async def cb_catalog(c: CallbackQuery, state: FSMContext):
    await state.clear()
    cats = db.q("SELECT * FROM categories ORDER BY position, id")
    rows = []
    for cat in cats:
        n = db.one("SELECT COUNT(*) n FROM tags WHERE category_id=? AND hidden=0", (cat["id"],))["n"]
        rows.append([btn(f"{cat['name']} ({n})", f"cat:{cat['id']}:0")])
    rows.append([btn(t("btn_menu"), "menu:main")])
    await show(c, t("txt_catalog") if cats else t("txt_catalog_empty"), kb(rows), "catalog")


@router.callback_query(F.data.startswith("cat:"))
async def cb_cat(c: CallbackQuery):
    _, cid, page = c.data.split(":")
    cid, page = int(cid), int(page)
    cat = db.one("SELECT * FROM categories WHERE id=?", (cid,))
    if not cat:
        return await c.answer(t("txt_cat_nf"), show_alert=True)
    tags = db.q("SELECT * FROM tags WHERE category_id=? AND hidden=0 ORDER BY id", (cid,))
    pages = max(1, (len(tags) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    chunk = tags[page * PAGE:(page + 1) * PAGE]
    rows, line = [], []
    for tg in chunk:
        line.append(tag_btn(tg, f"tag:{tg['id']}"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("◀️", f"cat:{cid}:{page - 1}"))
        if page < pages - 1:
            nav.append(btn(t("btn_next"), f"cat:{cid}:{page + 1}"))
        rows.append(nav)
    rows.append([btn(t("btn_to_cats"), "menu:catalog")])
    rows.append([btn(t("btn_menu"), "menu:main")])
    text = t("txt_cat_page", name=esc(cat["name"]), page=page + 1, pages=pages)
    if not tags:
        text = t("txt_cat_empty", name=esc(cat["name"]))
    manual = db.page_banner(cid, page)  # баннер, загруженный админом вручную, главнее автоматического
    gen = None
    if not manual and chunk:
        try:
            gen = await make_tags_banner([tg["name"] for tg in chunk])
        except Exception:
            logging.exception("Не удалось собрать баннер страницы тегов")
    await show(c, text, kb(rows), "tags", fid=manual or (None if gen else cat["banner"]), gen=gen)


async def make_tags_banner(names):
    """Баннер «Выбери тэг» для страницы раздела: названия тегов страницы, лишние плашки стёрты."""
    key = "tags:" + hashlib.sha1("\n".join(names).encode()).hexdigest()
    if key in _banner_ids:
        return key, b""
    return key, await asyncio.to_thread(banner.render_tags, names=names)


async def make_terms_banner(tag_name, banner_rows, unit="₽"):
    """Баннер «Выбери срок» под тег: название, сроки и цены (с учётом промокода)."""
    names = [r[0].lower() for r in banner_rows]
    hl = names.index("месяц") if "месяц" in names else (1 if len(banner_rows) >= 2 else None)
    args = dict(tag=tag_name, rows=banner_rows, highlight=hl, unit=unit)
    key = hashlib.sha1(repr(sorted(args.items(), key=lambda kv: kv[0])).encode()).hexdigest()
    if key in _banner_ids:
        return key, b""
    data = await asyncio.to_thread(banner.render_terms, **args)
    return key, data


async def make_id_banner(tag_name):
    """Баннер «Игровой ID» с названием покупаемого тега."""
    key = "id:" + hashlib.sha1(str(tag_name).encode()).hexdigest()
    if key in _banner_ids:
        return key, b""
    return key, await asyncio.to_thread(banner.render_id, tag=tag_name)


async def id_gen(price_id):
    """gen для экрана ввода ID по выбранному сроку (None, если баннер не получился)."""
    try:
        r = db.one("SELECT t.name FROM prices p JOIN tags t ON t.id=p.tag_id WHERE p.id=?", (price_id,))
        return await make_id_banner(r["name"]) if r else None
    except Exception:
        logging.exception("Не удалось создать баннер ID")
        return None


@router.callback_query(F.data.regexp(r"^tag:\d+$"))
async def cb_tag(c: CallbackQuery, state: FSMContext):
    await render_tag(c, state, int(c.data.split(":")[1]))


async def render_tag(c: CallbackQuery, state: FSMContext, tid: int, back_data=None, coins_mode=False):
    await state.clear()
    if coins_mode:  # покупка через «Магазин RECOINS»: везде только цены в койнах
        await state.update_data(coins_only=True)
    tag = db.one("SELECT * FROM tags WHERE id=? AND hidden=0", (tid,))
    if not tag:
        return await c.answer(t("txt_tag_nf"), show_alert=True)
    tcat = db.one("SELECT banner FROM categories WHERE id=?", (tag["category_id"],))
    prices = db.q("SELECT * FROM prices WHERE tag_id=? ORDER BY price_rub", (tid,))
    pr = db.active_promo(c.from_user.id)
    promo_pct = db.pct_for(pr, tid)
    disc = db.disc_of(tag)
    pct = db.combine_pct(promo_pct, disc)  # скидка тега + промокод
    rows = []
    sold_out = tag["slots"] is not None and tag["slots"] <= 0
    closed = bool(tag["closed"])
    site = False
    banner_rows = []
    for p in prices:
        if sold_out or closed:
            break
        if p["closed"]:  # закрыт только этот срок
            banner_rows.append((p["term"], None, True))
            rows.append([btn(f"🔒 {p['term']}", f"closed:{p['id']}")])
            continue
        if coins_mode:
            n_coins = db.coins_for(db.apply_discount(p["price_rub"], pct))
            banner_rows.append((p["term"], n_coins, False))
            rows.append([btn(f"{p['term']} | {fmt(n_coins)} 🪙", f"price:{p['id']}")])
            continue
        purl = links.norm_url(p["url"])
        if purl:
            site = True
            banner_rows.append((p["term"], p["price_rub"], False))
            rows.append([btn(f"{p['term']} | {fmt(p['price_rub'])}₽ ↗", url=purl)])
            continue
        rub = db.apply_discount(p["price_rub"], pct)
        gold = db.apply_discount(p["price_gold"], pct)
        stars = db.apply_discount(p["price_stars"], pct) if p["price_stars"] else None
        banner_rows.append((p["term"], rub, False, gold, stars))
        label = f"{p['term']} | {fmt(rub)}₽" + (f" / {fmt(gold)}г" if gold else "") + (f" / {fmt(stars)}⭐" if stars else "")
        rows.append([btn(label, f"price:{p['id']}")])
    if not coins_mode:
        rows.append([btn(t("btn_fav_del") if db.is_fav(c.from_user.id, tid) else t("btn_fav_add"), f"fav:{tid}")])
    if manager_url() and not coins_mode:
        rows.append([btn(t("btn_contact"), url=manager_url())])
    rows.append([btn(t("btn_back"), back_data or f"cat:{tag['category_id']}:0")])
    text = t("txt_tag_head", name=esc(tag_label(tag)), desc=esc(tag["description"]))
    if tag["slots"] is not None:
        text += "\n" + t("txt_slots", n=max(tag["slots"], 0))
    if closed:
        text += "\n\n" + t("txt_closed")
    elif sold_out:
        text += "\n\n" + t("txt_sold_out")
    elif site:
        text += "\n\n" + t("txt_site_pay")
    else:
        text += "\n\n" + t("txt_choose_term")
    if disc and not site:
        text += "\n\n" + t("txt_tag_discount", pct=disc, left=left_str(db.disc_left(tag)))
    if promo_pct and not site:
        text += "\n\n" + t("txt_promo_active", code=esc(pr[0]), pct=promo_pct)
    if coins_mode:
        text += "\n\n" + t("txt_coins_tag_balance", balance=fmt(db.coin_balance(c.from_user.id)))
    gen = None
    if not tag["banner"] and banner_rows:
        try:
            gen = await make_terms_banner(tag["name"], banner_rows, "RECOINS" if coins_mode else "₽")
        except Exception:
            logging.exception("Не удалось создать баннер сроков")
    await show(c, text, kb(rows), "terms", fid=tag["banner"] or (None if gen else (tcat["banner"] if tcat else None)),
               gen=gen)


@router.callback_query(F.data.regexp(r"^fav:\d+$"))
async def cb_fav_toggle(c: CallbackQuery, state: FSMContext):
    tid = int(c.data.split(":")[1])
    if not db.one("SELECT 1 FROM tags WHERE id=? AND hidden=0", (tid,)):
        return await c.answer(t("txt_tag_nf"), show_alert=True)
    db.fav_toggle(c.from_user.id, tid)
    await render_tag(c, state, tid)


@router.callback_query(F.data.regexp(r"^menu:fav(:\d+)?$"))
async def cb_fav(c: CallbackQuery, state: FSMContext):
    await state.clear()
    page = int(c.data.split(":")[2]) if c.data.count(":") == 2 else 0
    tags = db.fav_tags(c.from_user.id)
    if not tags:
        return await show(c, t("txt_fav_empty"),
                          kb([[btn(t("btn_to_catalog"), "menu:catalog")], [btn(t("btn_menu"), "menu:main")]]), "fav")
    pages = max(1, (len(tags) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    rows, line = [], []
    for tg in tags[page * PAGE:(page + 1) * PAGE]:
        line.append(tag_btn(tg, f"tag:{tg['id']}"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("◀️", f"menu:fav:{page - 1}"))
        if page < pages - 1:
            nav.append(btn(t("btn_next"), f"menu:fav:{page + 1}"))
        rows.append(nav)
    rows.append([btn(t("btn_menu"), "menu:main")])
    await show(c, t("txt_fav_title", page=page + 1, pages=pages), kb(rows), "fav")


@router.callback_query(F.data.regexp(r"^closed:\d+$"))
async def cb_closed(c: CallbackQuery):
    await c.answer(t("txt_term_closed_alert"), show_alert=True)


def gone_text(data):
    """Почему заказ нельзя продолжить: закрыт товар или сессия устарела."""
    pid = data.get("price_id")
    if pid and db.one("SELECT 1 FROM prices WHERE id=?", (pid,)) and is_closed(pid):
        return t("txt_closed")
    return t("txt_session_expired")


def is_closed(price_id):
    """True, если закрыт сам срок или весь тег."""
    r = db.one("SELECT p.closed pc, g.closed gc FROM prices p JOIN tags g ON g.id=p.tag_id WHERE p.id=?", (price_id,))
    return bool(r and (r["pc"] or r["gc"]))


@router.callback_query(F.data.startswith("price:"))
async def cb_price(c: CallbackQuery, state: FSMContext):
    pid = int(c.data.split(":")[1])
    if not db.one("SELECT 1 FROM prices WHERE id=?", (pid,)):
        return await c.answer(t("txt_term_nf"), show_alert=True)
    if is_closed(pid):
        return await c.answer(t("txt_closed_alert"), show_alert=True)
    tg = db.one("SELECT t.slots FROM prices p JOIN tags t ON t.id=p.tag_id WHERE p.id=?", (pid,))
    if tg["slots"] is not None and tg["slots"] <= 0:
        return await c.answer(t("txt_no_slots_alert"), show_alert=True)
    await state.update_data(price_id=pid)
    data = await state.get_data()
    if data.get("game_id"):  # смена срока из экрана заказа
        return await summary(c, state)
    await state.set_state(Order.game_id)
    p = db.one("SELECT tag_id FROM prices WHERE id=?", (pid,))
    other = f"ctag:{p['tag_id']}" if data.get("coins_only") else f"tag:{p['tag_id']}"
    await show(
        c,
        t("txt_enter_id"),
        kb([[btn(t("btn_other_term"), other)]]),
        "game_id",
        gen=await id_gen(pid),
    )


@router.message(Order.game_id)
async def got_game_id(m: Message, state: FSMContext):
    gid = (m.text or "").strip()
    if not re.fullmatch(r"\d{8,9}", gid):
        return await m.answer(t("txt_bad_id"))
    await state.update_data(game_id=gid)
    await state.set_state(None)
    await summary(m, state)


# ---------------- проверка заказа ----------------
def calc(data, user_id):
    p = db.one(
        "SELECT p.*, t.name tname, t.description tdesc, t.category_id FROM prices p "
        "JOIN tags t ON t.id=p.tag_id WHERE p.id=?", (data["price_id"],))
    if not p or is_closed(p["id"]):  # цену/тег удалили или закрыли, пока покупатель оформлял заказ
        return None, None, None, None
    pr = db.active_promo(user_id)
    promo_pct = db.pct_for(pr, p["tag_id"])
    if not promo_pct:  # промокод на другой тег — к этому заказу не применяется
        pr = None
    pct = db.combine_pct(promo_pct, db.tag_pct(p["tag_id"]))  # + скидка на тег
    return p, pr, db.apply_discount(p["price_rub"], pct), db.apply_discount(p["price_gold"], pct)


def stars_word(n):
    return banner.stars_word(n)


def extra_prices(p, pr):
    """Цены в звёздах и бустах для срока (или None). Скидка промокода действует только на звёзды;
    бусты — штучный товар, скидка к ним не применяется."""
    pct = db.total_pct(pr, p["tag_id"])
    stars = db.apply_discount(p["price_stars"], pct) if p["price_stars"] else None
    boosts = p["price_boosts"] or None
    return stars, boosts


def contact_for(key):
    """Юзернейм, куда писать при оплате звёздами/бустами; если не задан — менеджер."""
    return esc(links.norm_handle(db.get_setting(key)) or manager_handle())


HOLD_MIN = 30  # сколько минут держим место после показа реквизитов

_ICONS = (("карт", "💳"), ("сбп", "📱"), ("телефон", "📱"), ("банк", "🏦"), ("получател", "👤"))


def format_details(raw):
    """Реквизиты красиво: иконки, а номер карты / телефон — в <code> (копируется нажатием)."""
    out = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line:
            out.append("")
            continue
        label, sep, value = line.partition(":")
        low = label.lower()
        icon = next((i for k, i in _ICONS if k in low), "")
        if sep and ("карт" in low or "сбп" in low or "телефон" in low):
            digits = re.sub(r"[^\d+]", "", value)
            if len(re.sub(r"\D", "", digits)) >= 10:
                pretty = esc(value.strip())
                shown = f"<code>{esc(digits)}</code>"
                if "сбп" in low or "телефон" in low:
                    shown += f" ({pretty})" if pretty != esc(digits) else ""
                line = f"{esc(label)}: {shown}"
                out.append(f"{icon} {line}".strip())
                continue
        out.append(f"{icon} {esc(line)}".strip())
    return "\n".join(out)


async def summary_coins(target, state, data, p, pr, rub, user_id, lines):
    """Сводка заказа из «Магазина RECOINS»: только цена и оплата койнами."""
    coins = db.coins_for(rub)
    if pr:
        lines.append(t("txt_order_promo", code=esc(pr[0]), pct=pr[1]))
    lines.append(t("txt_order_coins", price=fmt(coins), balance=fmt(db.coin_balance(user_id))))
    lines.append("\n" + t("txt_order_choose_pay"))
    rows = [[btn(t("btn_pay_coins", price=fmt(coins)), "pay:coins")],
            [btn(t("btn_other_term"), f"tag:{p['tag_id']}_keep"), btn(t("btn_other_id"), "order:chid")],
            [btn(t("btn_coins_menu"), "menu:main")]]
    await show(target, "\n".join(lines), kb(rows), "order")


async def summary(target, state: FSMContext):
    data = await state.get_data()
    user_id = target.from_user.id
    if not data.get("price_id") or not data.get("game_id"):
        return await show(target, t("txt_session_expired"), main_menu())
    p, pr, rub, gold = calc(data, user_id)
    if not p:
        await state.clear()
        return await show(target, gone_text(data), main_menu())
    lines = [
        t("txt_order_title") + "\n",
        t("txt_order_id", id=esc(data["game_id"])),
        t("txt_order_tag", tag=f"{esc(p['tname'])} {esc(p['tdesc'])}".strip()),
        t("txt_order_term", term=esc(p["term"])),
    ]
    if data.get("coins_only"):
        return await summary_coins(target, state, data, p, pr, rub, user_id, lines)
    if pr:
        lines.append(t("txt_order_promo", code=esc(pr[0]), pct=pr[1]))
    if rub != p["price_rub"]:  # промокод и/или скидка на тег
        lines.append(t("txt_order_card", price=f"<s>{fmt(p['price_rub'])}</s> <b>{fmt(rub)} ₽</b>"))
    else:
        lines.append(t("txt_order_card", price=f"{fmt(rub)} ₽"))
    if gold:
        lines.append(t("txt_order_gold", price=fmt(gold)))
    stars, boosts = extra_prices(p, pr)
    if stars:
        lines.append(t("txt_order_stars", price=fmt(stars)))
    if boosts:
        lines.append(t("txt_order_boost", price=fmt(boosts)))
    coins = db.coins_for(rub)
    reward = db.coins_reward(p, rub)
    if reward > 0:
        lines.append(t("txt_order_reward", n=fmt(reward)))
    lines.append(t("txt_order_coins", price=fmt(coins), balance=fmt(db.coin_balance(user_id))))
    lines.append("\n" + t("txt_order_choose_pay"))
    rows = [[btn(t("btn_pay_card", price=fmt(rub)), "pay:card")]]
    rows.append([btn(t("btn_pay_coins", price=fmt(coins)), "pay:coins")])
    if gold:
        rows.append([btn(t("btn_pay_gold", price=fmt(gold)), "pay:gold")])
    if stars:
        rows.append([btn(t("btn_pay_stars", price=fmt(stars)), "pay:stars")])
    if boosts:
        rows.append([btn(t("btn_pay_boost", price=fmt(boosts)), "pay:boost")])
    rows.append([btn(t("btn_apply_promo"), "promo:order")])
    rows.append([btn(t("btn_other_term"), f"tag:{p['tag_id']}_keep"), btn(t("btn_other_id"), "order:chid")])
    rows.append([btn(t("btn_menu"), "menu:main")])
    gen = None
    try:
        args = dict(game_id=data["game_id"], tag=p["tname"], term=p["term"], rub=rub, gold=gold)
        key = "ord:" + hashlib.sha1(repr(sorted(args.items())).encode()).hexdigest()
        gen = (key, b"" if key in _banner_ids else await asyncio.to_thread(banner.render_order, **args))
    except Exception:
        logging.exception("Не удалось создать баннер заказа")
    await show(target, "\n".join(lines), kb(rows), "order", gen=gen)


# «Изменить срок» из заказа: открываем страницу тега, game_id сохраняем
@router.callback_query(F.data.regexp(r"^tag:\d+_keep$"))
async def cb_tag_keep(c: CallbackQuery, state: FSMContext):
    tid = int(c.data.split(":")[1].split("_")[0])
    data = await state.get_data()
    gid = data.get("game_id")
    coins_only = bool(data.get("coins_only"))
    await state.clear()
    if gid:
        await state.update_data(game_id=gid)
    if coins_only:
        await state.update_data(coins_only=True)
    tag = db.one("SELECT * FROM tags WHERE id=?", (tid,))
    prices = db.q("SELECT * FROM prices WHERE tag_id=? ORDER BY price_rub", (tid,))
    pr = db.active_promo(c.from_user.id)
    pct = db.total_pct(pr, tid)
    rows = []
    for p in prices:
        if tag["closed"]:
            break
        if p["closed"]:
            rows.append([btn(f"🔒 {p['term']}", f"closed:{p['id']}")])
            continue
        rub = db.apply_discount(p["price_rub"], pct)
        gold = db.apply_discount(p["price_gold"], pct)
        if coins_only:
            rows.append([btn(f"{p['term']} | {fmt(db.coins_for(rub))} 🪙", f"price:{p['id']}")])
            continue
        rows.append([btn(f"{p['term']} | {fmt(rub)}₽" + (f" / {fmt(gold)}г" if gold else ""), f"price:{p['id']}")])
    rows.append([btn(t("btn_menu"), "menu:main")])
    await show(c, t("txt_choose_new_term", name=esc(tag["name"])) + (("\n\n" + t("txt_closed")) if tag["closed"] else ""), kb(rows))


@router.callback_query(F.data == "order:chid")
async def cb_chid(c: CallbackQuery, state: FSMContext):
    await state.update_data(game_id=None)
    await state.set_state(Order.game_id)
    await show(c, t("txt_enter_id_new"), banner="game_id", gen=await id_gen((await state.get_data()).get("price_id")))


# ---------------- промокод ----------------
@router.callback_query(F.data.in_({"menu:promo", "promo:order"}))
async def cb_promo(c: CallbackQuery, state: FSMContext):
    from_order = c.data == "promo:order"
    await state.update_data(promo_from_order=from_order)
    await state.set_state(Order.promo)
    cur = db.active_promo(c.from_user.id)
    text = t("txt_promo_screen")
    if cur:
        ctag = db.promo_tag(cur[0])
        text += "\n\n" + (t("txt_promo_current_tag", code=esc(cur[0]), pct=cur[1], tag=esc(ctag["name"])) if ctag
                          else t("txt_promo_current", code=esc(cur[0]), pct=cur[1]))
    back = "order:back" if from_order else "menu:main"
    await show(c, text, kb([[btn(t("btn_back"), back)]]), "promo")


@router.callback_query(F.data == "order:back")
async def cb_order_back(c: CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await summary(c, state)


@router.message(Order.promo)
async def got_promo(m: Message, state: FSMContext):
    code = (m.text or "").strip().upper()
    percent, err = db.check_promo(code, m.from_user.id)
    if err:
        return await m.answer(t("txt_promo_err", error=t("txt_err_promo_" + err)), reply_markup=back_menu())
    db.activate_promo(m.from_user.id, code)
    data = await state.get_data()
    await state.set_state(None)
    ptag = db.promo_tag(code)  # None — промокод на все теги
    if data.get("promo_from_order") and data.get("price_id") and data.get("game_id"):
        price = db.one("SELECT tag_id FROM prices WHERE id=?", (data["price_id"],))
        if ptag and price and price["tag_id"] != ptag["id"]:
            await m.answer(t("txt_promo_wrong_tag", code=esc(code), tag=esc(ptag["name"])))
        elif ptag:
            await m.answer(t("txt_promo_ok_order_tag", code=esc(code), pct=percent, tag=esc(ptag["name"])))
        else:
            await m.answer(t("txt_promo_ok_order", code=esc(code), pct=percent))
        return await summary(m, state)
    await m.answer(
        t("txt_promo_ok_tag", code=esc(code), pct=percent, tag=esc(ptag["name"])) if ptag
        else t("txt_promo_ok", code=esc(code), pct=percent),
        reply_markup=kb([[btn(t("btn_to_catalog"), "menu:catalog")], [btn(t("btn_menu"), "menu:main")]]),
    )


# ---------------- оплата ----------------
@router.callback_query(F.data.in_({"pay:card", "pay:gold", "pay:stars", "pay:boost"}))
async def cb_pay(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("price_id") or not data.get("game_id"):
        return await show(c, t("txt_session_expired"), main_menu())
    p, pr, rub, gold = calc(data, c.from_user.id)
    if not p:
        await state.clear()
        return await show(c, gone_text(data), main_menu())
    if p["price_gold"] is None and c.data == "pay:gold":
        return await c.answer(t("txt_session_expired"), show_alert=True)
    method = c.data.split(":")[1]
    stars, boosts = extra_prices(p, pr)
    amount = {"card": rub, "gold": gold, "stars": stars, "boost": boosts}[method]
    if not amount:  # админ убрал этот способ оплаты, пока покупатель оформлял заказ
        return await c.answer(t("txt_session_expired"), show_alert=True)
    order_no = data.get("order_no") or db.reserve_order_number()
    if method in ("stars", "boost"):
        key = "txt_pay_stars" if method == "stars" else "txt_pay_boost"
        text = t(key, price=fmt(amount), contact=contact_for("stars_contact" if method == "stars" else "boost_contact"),
                 tag=esc(p["tname"]), term=esc(p["term"]), minutes=HOLD_MIN,
                 order=f"PT-{order_no}", game_id=esc(data["game_id"]))
    elif method == "card":
        text = t("txt_pay_card", price=fmt(amount), details=format_details(db.get_setting("payment_details")),
                 tag=esc(f"{p['tname']}"), term=esc(p["term"]), minutes=HOLD_MIN,
                 order=f"PT-{order_no}")
    else:
        text = t("txt_pay_gold", price=fmt(amount), manager=mgr(),
                 tag=esc(f"{p['tname']}"), term=esc(p["term"]), minutes=HOLD_MIN,
                 order=f"PT-{order_no}", game_id=esc(data["game_id"]))
    await state.update_data(method=method, order_no=order_no, pay_started=int(time.time()))
    gen = None
    try:
        sub = t("bn_pay_gold_sub") if method == "gold" else (
            t("bn_pay_other_sub") if method in ("stars", "boost") else None)
        unit = {"card": "₽", "gold": "г", "stars": stars_word(amount), "boost": "буст."}[method]
        args = dict(amount=amount, unit=unit, subtitle=sub)
        key = "pay:" + hashlib.sha1(repr(sorted(args.items(), key=lambda kv: kv[0])).encode()).hexdigest()
        gen = (key, b"" if key in _banner_ids else await asyncio.to_thread(banner.render_pay, **args))
    except Exception:
        logging.exception("Не удалось создать баннер оплаты")
    await show(c, text, kb([
        [btn(t("btn_paid"), "pay:done")],
        [btn(t("btn_back"), "order:back")],
    ]), "pay", gen=gen)


@router.callback_query(F.data == "pay:done")
async def cb_done(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("price_id") or not data.get("game_id") or not data.get("method"):
        return await show(c, t("txt_session_expired"), main_menu())
    p, pr, rub, gold = calc(data, c.from_user.id)
    if not p:
        await state.clear()
        return await show(c, gone_text(data), main_menu())
    tg = db.one("SELECT slots FROM tags WHERE id=?", (p["tag_id"],))
    if tg and tg["slots"] is not None and tg["slots"] <= 0:
        return await c.answer(t("txt_no_slots_alert"), show_alert=True)
    if time.time() - data.get("pay_started", 0) > HOLD_MIN * 60:
        await state.set_state(None)
        await c.answer(t("txt_pay_expired", minutes=HOLD_MIN), show_alert=True)
        return await summary(c, state)
    await state.set_state(Order.proof)
    key = {"card": "txt_proof_card", "gold": "txt_proof_gold"}.get(data["method"], "txt_proof_shot")
    await show(c, t(key), kb([[btn(t("btn_back"), "pay:back")]]))


@router.callback_query(F.data == "pay:back")
async def cb_pay_back(c: CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await summary(c, state)


@router.message(Order.proof)
async def got_proof(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    file_id = None
    card_text = None
    if m.photo:
        file_id = m.photo[-1].file_id
    elif m.document and (m.document.mime_type or "").startswith("image/"):
        file_id = m.document.file_id
    elif m.text and data.get("method") == "card":
        digits = re.sub(r"\D", "", m.text)
        if 4 <= len(digits) <= 19 and len(m.text) <= 40:  # номер карты (или последние цифры) отправителя
            card_text = " ".join(digits[i:i + 4] for i in range(0, len(digits), 4))
    if not file_id and not card_text:
        bad = {"card": "txt_proof_bad", "gold": "txt_proof_bad_gold"}.get(data.get("method"), "txt_proof_bad_shot")
        return await m.answer(t(bad), reply_markup=kb([[btn(t("btn_back"), "pay:back")]]))
    if not data.get("price_id") or not data.get("game_id") or not data.get("method"):
        await state.clear()
        return await m.answer(t("txt_session_expired"), reply_markup=main_menu())
    if time.time() - data.get("pay_started", 0) > HOLD_MIN * 60:
        await state.set_state(None)
        await m.answer(t("txt_pay_expired", minutes=HOLD_MIN))
        return await summary(m, state)
    p, pr, rub, gold = calc(data, m.from_user.id)
    if not p:
        await state.clear()
        return await m.answer(gone_text(data), reply_markup=main_menu())
    tg = db.one("SELECT slots FROM tags WHERE id=?", (p["tag_id"],))
    if tg and tg["slots"] is not None and tg["slots"] <= 0:
        await state.clear()
        return await m.answer(t("txt_no_slots_alert"), reply_markup=main_menu())
    method = data["method"]
    stars, boosts = extra_prices(p, pr)
    amount = {"card": rub, "gold": gold, "stars": stars, "boost": boosts}.get(method)
    if not amount:
        await state.clear()
        return await m.answer(t("txt_session_expired"), reply_markup=main_menu())
    # промокод тратится, только если скидка реально применена (к бустам скидка не действует)
    promo_code = pr[0] if pr and method != "boost" else None
    oid = db.ex(
        "INSERT INTO orders(user_id,username,game_id,tag,term,method,amount,promo,created_at,tag_id,proof,id,price_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (m.from_user.id, m.from_user.username, data["game_id"], f"{p['tname']} {p['tdesc']}".strip(),
         p["term"], method, amount, promo_code, int(time.time()), p["tag_id"], file_id or ("card:" + card_text), data.get("order_no"), p["id"]),
    )
    if promo_code:
        db.consume_promo(m.from_user.id, promo_code)
    await state.clear()
    await m.answer(t("txt_order_created", id=oid), reply_markup=back_menu())
    uname = f"@{m.from_user.username}" if m.from_user.username else f"id{m.from_user.id}"
    text = (f"🆕 <b>Новый заказ #{oid}</b>\n\n👤 {esc(uname)}\n🎮 ID: {esc(data['game_id'])}\n"
            f"🏷 {esc(p['tname'])} · {esc(p['term'])}\n"
            f"💵 {db.money(amount, method)} ({db.METHOD_NAME[method]})"
            + (f"\n🎟 Промокод: {esc(promo_code)}" if promo_code else ""))
    markup = kb([[btn("✅ Подтвердить", f"adm:ok:{oid}"), btn("❌ Отклонить", f"adm:no:{oid}")]])
    for aid in db.admin_ids():
        try:
            if file_id:
                await bot.send_photo(aid, file_id, caption=text, reply_markup=markup)
            else:
                await bot.send_message(aid, text + f"\n💳 Карта отправителя: <code>{esc(card_text)}</code>",
                                       reply_markup=markup)
        except Exception:
            pass


# ---------------- Магазин RECOINS ----------------
COIN_ICONS = {"earn": "➕", "spend": "➖", "refund": "↩️", "admin": "🎁", "bonus": "🎁"}
COIN_WORDS = {"earn": "начислено", "spend": "потрачено", "refund": "возврат", "admin": "от администрации", "bonus": "ежедневный бонус"}


@router.callback_query(F.data == "menu:coins")
async def cb_coins(c: CallbackQuery, state: FSMContext):
    await state.clear()
    text = t("txt_coins_shop", balance=fmt(db.coin_balance(c.from_user.id)), percent=db.coin_percent(), mult=db.coin_mult())
    await show(c, text, kb([
        [btn(t("btn_coins_buy"), "coins:buy:0")],
        [btn(t("btn_coins_hist"), "coins:hist")],
        [btn(t("btn_coins_menu"), "menu:main")],
    ]), "coins")


@router.callback_query(F.data.regexp(r"^coins:buy:\d+$"))
async def cb_coins_buy(c: CallbackQuery, state: FSMContext):
    await state.clear()
    page = int(c.data.split(":")[2])
    tags = db.q("SELECT t.* FROM tags t JOIN categories c ON c.id=t.category_id WHERE t.hidden=0 AND COALESCE(t.coin_hidden,0)=0 "
                "ORDER BY c.position, c.id, t.id")
    pages = max(1, (len(tags) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    chunk = tags[page * PAGE:(page + 1) * PAGE]
    rows, line = [], []
    for tg in chunk:
        line.append(tag_btn(tg, f"ctag:{tg['id']}"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("◀️", f"coins:buy:{page - 1}"))
        if page < pages - 1:
            nav.append(btn(t("btn_next"), f"coins:buy:{page + 1}"))
        rows.append(nav)
    rows.append([btn(t("btn_back"), "menu:coins")])
    rows.append([btn(t("btn_coins_menu"), "menu:main")])
    text = t("txt_coins_buy", balance=fmt(db.coin_balance(c.from_user.id)), page=page + 1, pages=pages) if tags \
        else t("txt_coins_no_tags")
    gen = None
    if chunk:  # тот же баннер, что и в «Каталоге тэгов»
        try:
            gen = await make_tags_banner([tg["name"] for tg in chunk])
        except Exception:
            logging.exception("Не удалось собрать баннер магазина монет")
    await show(c, text, kb(rows), "tags", gen=gen)


@router.callback_query(F.data.regexp(r"^ctag:\d+$"))
async def cb_coins_tag(c: CallbackQuery, state: FSMContext):
    tg = db.one("SELECT coin_hidden FROM tags WHERE id=?", (int(c.data.split(":")[1]),))
    if not tg or tg["coin_hidden"]:  # админ убрал тег из магазина, пока открыт старый список
        return await c.answer(t("txt_tag_nf"), show_alert=True)
    await render_tag(c, state, int(c.data.split(":")[1]), back_data="coins:buy:0", coins_mode=True)


@router.callback_query(F.data == "coins:hist")
async def cb_coins_hist(c: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = c.from_user.id
    rows = db.q("SELECT * FROM coin_log WHERE user_id=? ORDER BY id DESC LIMIT 15", (uid,))
    earned = db.one("SELECT COALESCE(SUM(delta),0) s FROM coin_log WHERE user_id=? AND (kind IN ('earn','bonus') OR (kind='admin' AND delta>0))", (uid,))["s"]
    spent = -db.one("SELECT COALESCE(SUM(delta),0) s FROM coin_log WHERE user_id=? AND (kind IN ('spend','refund') OR (kind='admin' AND delta<0))", (uid,))["s"]
    lines = [t("txt_coins_hist_title", balance=fmt(db.coin_balance(uid)), earned=fmt(earned), spent=fmt(spent))]
    if not rows:
        lines.append(t("txt_coins_hist_empty"))
    for r in rows:
        sign = "+" if r["delta"] > 0 else "−"
        what = " ".join(x for x in (r["tag"], r["term"]) if x)
        tail = (f" · {esc(what)}" if what else "") + (f" · заказ #{r['order_id']}" if r["order_id"] else "")
        lines.append(f"{COIN_ICONS.get(r['kind'], '•')} {sign}{fmt(abs(r['delta']))} 🪙 — {COIN_WORDS.get(r['kind'], r['kind'])}{tail}")
    await show(c, "\n".join(lines), kb([[btn(t("btn_back"), "menu:coins")], [btn(t("btn_coins_menu"), "menu:main")]]), "coins")


@router.callback_query(F.data == "pay:coins")
async def cb_pay_coins(c: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not data.get("price_id") or not data.get("game_id"):
        return await show(c, t("txt_session_expired"), main_menu())
    p, pr, rub, gold = calc(data, c.from_user.id)
    if not p:
        await state.clear()
        return await show(c, gone_text(data), main_menu())
    tg = db.one("SELECT slots FROM tags WHERE id=?", (p["tag_id"],))
    if tg and tg["slots"] is not None and tg["slots"] <= 0:
        return await c.answer(t("txt_no_slots_alert"), show_alert=True)
    amount = db.coins_for(rub)
    balance = db.coin_balance(c.from_user.id)
    if balance < amount:
        return await c.answer(t("txt_coins_not_enough", need=fmt(amount), balance=fmt(balance)), show_alert=True)
    promo_code = pr[0] if pr else None
    order_no = data.get("order_no") or db.reserve_order_number()
    full_tag = f"{p['tname']} {p['tdesc']}".strip()
    oid = db.ex(
        "INSERT INTO orders(user_id,username,game_id,tag,term,method,amount,promo,created_at,tag_id,proof,id,price_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (c.from_user.id, c.from_user.username, data["game_id"], full_tag, p["term"], "coins", amount, promo_code,
         int(time.time()), p["tag_id"], "coins", order_no, p["id"]))
    db.coin_add(c.from_user.id, -amount, "spend", p["tname"], p["term"], oid)
    if promo_code:
        db.consume_promo(c.from_user.id, promo_code)
    await state.clear()
    await show(c, t("txt_order_created", id=oid), back_menu())
    uname = f"@{c.from_user.username}" if c.from_user.username else f"id{c.from_user.id}"
    text = (f"🆕 <b>Новый заказ #{oid}</b>\n\n👤 {esc(uname)}\n🎮 ID: {esc(data['game_id'])}\n"
            f"🏷 {esc(p['tname'])} · {esc(p['term'])}\n"
            f"💵 {db.money(amount, 'coins')} ({db.METHOD_NAME['coins']}) — монеты уже списаны"
            + (f"\n🎟 Промокод: {esc(promo_code)}" if promo_code else ""))
    markup = kb([[btn("✅ Подтвердить", f"adm:ok:{oid}"), btn("❌ Отклонить", f"adm:no:{oid}")]])
    for aid in db.admin_ids():
        try:
            await bot.send_message(aid, text, reply_markup=markup)
        except Exception:
            pass


# ---------------- ежедневный бонус ----------------
def left_text(sec):
    h, m = sec // 3600, (sec % 3600 + 59) // 60
    if m == 60:
        h, m = h + 1, 0
    return f"{h} ч {m} мин" if h else f"{max(m, 1)} мин"


def bonus_kb(ready):
    rows = [[btn(t("btn_bonus_claim"), "bonus:claim")]] if ready else []
    rows += [[btn(t("btn_coins"), "menu:coins")], [btn(t("btn_menu"), "menu:main")]]
    return kb(rows)


@router.callback_query(F.data == "menu:bonus")
async def cb_bonus(c: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = c.from_user.id
    left = db.bonus_left(uid)
    lo, hi = db.bonus_range()
    bal = fmt(db.coin_balance(uid))
    text = t("txt_bonus_wait", left=left_text(left), balance=bal) if left else t("txt_bonus_ready", min=lo, max=hi, balance=bal)
    await show(c, text, bonus_kb(not left), "bonus")


@router.callback_query(F.data == "bonus:claim")
async def cb_bonus_claim(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    n = db.bonus_claim(uid)
    if n is None:  # уже забрал (или двойной тап)
        return await cb_bonus(c, state)
    await show(c, t("txt_bonus_got", n=n, balance=fmt(db.coin_balance(uid))), bonus_kb(False), "bonus")


# ---------------- доступ: бан и режим работ ----------------
class Gate(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if data.get("bot"):
            favorites.ensure_watcher(data["bot"])  # фоновая проверка избранного (запускается один раз)
        u = data.get("event_from_user")
        if u and not db.is_admin(u.id):
            r = db.one("SELECT banned FROM users WHERE user_id=?", (u.id,))
            if r and r["banned"]:
                return
            if db.get_setting("maintenance") == "1":
                text = t("maintenance_text")
                if isinstance(event, CallbackQuery):
                    await event.answer(text, show_alert=True)
                elif isinstance(event, Message):
                    await event.answer(text)
                return
        return await handler(event, data)


router.message.outer_middleware(Gate())
router.callback_query.outer_middleware(Gate())


# ---------------- отзывы в канал ----------------
class Review(StatesGroup):
    text = State()


def buyer_hash(user_id: int) -> str:
    return "#" + hashlib.sha1(f"rv{user_id}".encode()).hexdigest()[:4].upper()


def rating_kb(oid):
    return kb([[btn("⭐" * n, f"rv:{oid}:{n}") for n in (1, 2, 3)],
               [btn("⭐" * n, f"rv:{oid}:{n}") for n in (4, 5)]])


async def send_review_prompt(bot: Bot, o):
    """Просит покупателя оценить заказ (если админ настроил канал отзывов)."""
    if not db.get_setting("reviews_chat"):
        return
    await bot.send_message(o["user_id"], t("txt_rv_prompt", id=o["id"]),
                           reply_markup=rating_kb(o["id"]))


@router.callback_query(F.data.regexp(r"^rv:start:\d+$"))
async def cb_rv_start(c: CallbackQuery):
    oid = int(c.data.split(":")[2])
    await show(c, t("txt_rv_prompt2", id=oid), rating_kb(oid))


@router.callback_query(F.data.regexp(r"^rv:\d+:[1-5]$"))
async def cb_rv_rate(c: CallbackQuery, state: FSMContext):
    _, oid, n = c.data.split(":")
    o = db.one("SELECT * FROM orders WHERE id=? AND user_id=?", (int(oid), c.from_user.id))
    if not o or o["status"] != "paid" or o["reviewed"]:
        return await c.answer(t("txt_rv_unavailable"), show_alert=True)
    await state.set_state(Review.text)
    await state.update_data(oid=int(oid), rating=int(n))
    await show(c, t("txt_rv_text", stars="⭐" * int(n)),
               kb([[btn(t("btn_skip"), "rv:skip")]]))


@router.callback_query(F.data == "rv:skip")
async def cb_rv_skip(c: CallbackQuery, state: FSMContext, bot: Bot):
    await publish_review(c, state, bot, "")


@router.message(Review.text)
async def got_review(m: Message, state: FSMContext, bot: Bot):
    await publish_review(m, state, bot, (m.text or "").strip()[:500])


async def publish_review(target, state, bot: Bot, text):
    data = await state.get_data()
    await state.clear()
    o = db.one("SELECT * FROM orders WHERE id=? AND user_id=?", (data.get("oid"), target.from_user.id))
    chat = links.norm_chat(db.get_setting("reviews_chat") or "") or ""
    if not o or o["reviewed"] or not chat:
        return await show(target, t("txt_rv_na"), back_menu())
    r = data["rating"]
    post = t("txt_rv_post", stars="⭐" * r, rating=r, tag=esc(o["tag"].split()[0]), term=esc(o["term"]),
             buyer=buyer_hash(o["user_id"]))
    if text:
        post += "\n\n" + t("txt_rv_quote", text=esc(text))
    markup = None
    try:
        me = await bot.me()
        markup = kb([[btn(t("btn_buy_tag"), url=f"https://t.me/{me.username}")]])
        await bot.send_message(int(chat) if chat.lstrip("-").isdigit() else chat, post, reply_markup=markup)
        db.ex("UPDATE orders SET reviewed=1 WHERE id=?", (o["id"],))
    except Exception as e:
        for aid in db.admin_ids():
            try:
                await bot.send_message(aid, f"⚠️ Не удалось опубликовать отзыв в канал <code>{esc(chat)}</code>: {esc(e)}\n"
                                            "Проверь, что бот добавлен в канал админом с правом публикации.")
            except Exception:
                pass
    await show(target, t("txt_rv_thanks"), back_menu())
