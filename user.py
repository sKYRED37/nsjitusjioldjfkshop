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


def btn(text, data=None, url=None):
    return InlineKeyboardButton(text=text, callback_data=data, url=url)


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
        [btn(g("btn_promo"), "menu:promo"), btn(g("btn_orders"), "menu:orders")],
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
        line.append(btn(("🔒 " if tg["closed"] else "") + tg["name"], f"tag:{tg['id']}"))
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


async def make_terms_banner(tag_name, banner_rows):
    """Баннер «Выбери срок» под тег: название, сроки и цены (с учётом промокода)."""
    names = [r[0].lower() for r in banner_rows]
    hl = names.index("месяц") if "месяц" in names else (1 if len(banner_rows) >= 2 else None)
    args = dict(tag=tag_name, rows=banner_rows, highlight=hl)
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
    await state.clear()
    tid = int(c.data.split(":")[1])
    tag = db.one("SELECT * FROM tags WHERE id=? AND hidden=0", (tid,))
    if not tag:
        return await c.answer(t("txt_tag_nf"), show_alert=True)
    tcat = db.one("SELECT banner FROM categories WHERE id=?", (tag["category_id"],))
    prices = db.q("SELECT * FROM prices WHERE tag_id=? ORDER BY price_rub", (tid,))
    pr = db.active_promo(c.from_user.id)
    pct = db.pct_for(pr, tid)
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
    if manager_url():
        rows.append([btn(t("btn_contact"), url=manager_url())])
    rows.append([btn(t("btn_back"), f"cat:{tag['category_id']}:0")])
    text = t("txt_tag_head", name=esc(tag["name"]), desc=esc(tag["description"]))
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
    if pct and not site:
        text += "\n\n" + t("txt_promo_active", code=esc(pr[0]), pct=pct)
    gen = None
    if not tag["banner"] and banner_rows:
        try:
            gen = await make_terms_banner(tag["name"], banner_rows)
        except Exception:
            logging.exception("Не удалось создать баннер сроков")
    await show(c, text, kb(rows), "terms", fid=tag["banner"] or (None if gen else (tcat["banner"] if tcat else None)),
               gen=gen)


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
    await show(
        c,
        t("txt_enter_id"),
        kb([[btn(t("btn_other_term"), f"tag:{p['tag_id']}")]]),
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
    pct = db.pct_for(pr, p["tag_id"])
    if not pct:  # промокод на другой тег — к этому заказу не применяется
        pr = None
    return p, pr, db.apply_discount(p["price_rub"], pct), db.apply_discount(p["price_gold"], pct)


def stars_word(n):
    return banner.stars_word(n)


def extra_prices(p, pr):
    """Цены в звёздах и бустах для срока (или None). Скидка промокода действует только на звёзды;
    бусты — штучный товар, скидка к ним не применяется."""
    pct = pr[1] if pr else 0
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
    if pr:
        lines.append(t("txt_order_promo", code=esc(pr[0]), pct=pr[1]))
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
    lines.append("\n" + t("txt_order_choose_pay"))
    rows = [[btn(t("btn_pay_card", price=fmt(rub)), "pay:card")]]
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
    await state.clear()
    if gid:
        await state.update_data(game_id=gid)
    tag = db.one("SELECT * FROM tags WHERE id=?", (tid,))
    prices = db.q("SELECT * FROM prices WHERE tag_id=? ORDER BY price_rub", (tid,))
    pr = db.active_promo(c.from_user.id)
    pct = db.pct_for(pr, tid)
    rows = []
    for p in prices:
        if tag["closed"]:
            break
        if p["closed"]:
            rows.append([btn(f"🔒 {p['term']}", f"closed:{p['id']}")])
            continue
        rub = db.apply_discount(p["price_rub"], pct)
        gold = db.apply_discount(p["price_gold"], pct)
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
        "INSERT INTO orders(user_id,username,game_id,tag,term,method,amount,promo,created_at,tag_id,proof,id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (m.from_user.id, m.from_user.username, data["game_id"], f"{p['tname']} {p['tdesc']}".strip(),
         p["term"], method, amount, promo_code, int(time.time()), p["tag_id"], file_id or ("card:" + card_text), data.get("order_no")),
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


# ---------------- доступ: бан и режим работ ----------------
class Gate(BaseMiddleware):
    async def __call__(self, handler, event, data):
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
