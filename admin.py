import asyncio
import re
import secrets
import time

from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.exceptions import TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, FSInputFile

import db
from user import esc, fmt, kb, btn, show, send_review_prompt
import links
import texts
from texts import t

router = Router()

# Все хендлеры этого роутера доступны ТОЛЬКО администраторам, добавленным в боте.
router.message.filter(lambda m: m.from_user is not None and db.is_admin(m.from_user.id))
router.callback_query.filter(lambda c: db.is_admin(c.from_user.id))


class A(StatesGroup):
    cat_name = State()
    tag_new = State()
    prices_new = State()
    fast_tag = State()
    promo_new = State()
    promo_tag = State()
    admin_add = State()
    setting = State()
    broadcast = State()
    reply = State()
    ref_new = State()
    slots = State()
    banner = State()
    ent_banner = State()
    page_banner = State()
    cat_rename = State()
    tag_edit = State()
    price_edit = State()
    order_search = State()
    user_search = State()
    text_edit = State()


LINK_SETTINGS = {
    "manager": (links.norm_handle, "Пришли юзернейм менеджера: @username (можно и ссылку t.me/username)."),
    "reviews_url": (links.norm_url, "Пришли ссылку, например https://t.me/my_reviews или @my_reviews. «-» — убрать кнопку."),
    "channel_url": (links.norm_url, "Пришли ссылку, например https://t.me/my_channel или @my_channel. «-» — убрать кнопку."),
    "reviews_chat": (links.norm_chat, "Пришли @канал, ссылку t.me/канал или числовой ID вида -1001234567890. «-» — отключить."),
    "stars_contact": (links.norm_handle, "Пришли юзернейм: @username. «-» — писать менеджеру."),
    "boost_contact": (links.norm_handle, "Пришли юзернейм: @username. «-» — писать менеджеру."),
}

SETTING_NAMES = {
    "manager": "Менеджер (@username)",
    "reviews_url": "Ссылка на отзывы (кнопка в меню)",
    "channel_url": "Ссылка на канал (кнопка в «Поддержке»)",
    "payment_details": "Реквизиты для оплаты",
    "reviews_chat": "Канал для отзывов (@канал или -100…ID)",
    "stars_contact": "⭐ Юз, куда писать при оплате звёздами",
    "boost_contact": "🚀 Юз, куда писать при оплате бустами",
}


def panel_kb():
    pending = db.one("SELECT COUNT(*) n FROM orders WHERE status='pending'")["n"]
    maint = db.get_setting("maintenance") == "1"
    return kb([
        [btn(f"📦 Заказы ({pending})", "adm:orders"), btn("📊 Статистика", "adm:stats")],
        [btn("🗂 Каталог", "adm:catalog"), btn("🎟 Промокоды", "adm:promos")],
        [btn("👤 Пользователи", "adm:users"), btn("👥 Админы", "adm:admins")],
        [btn("✏️ Тексты бота", "adm:tx"), btn("🖼 Баннеры", "adm:banners")],
        [btn("⚙️ Настройки", "adm:settings"), btn("🔗 Рефералки", "adm:refs")],
        [btn("📢 Рассылка", "adm:bc"), btn("⭐ Отзывы-канал", "adm:rvchan")],
        [btn("🔧 Режим работ: ВКЛ" if maint else "🔧 Режим работ: выкл", "adm:maint"), btn("💾 Бэкап БД", "adm:backup")],
        [btn("✖️ Закрыть", "adm:close")],
    ])


@router.message(Command("admin"))
async def admin_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🛠 <b>Админ-панель</b>", reply_markup=panel_kb())


@router.callback_query(F.data == "adm:panel")
async def cb_panel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "🛠 <b>Админ-панель</b>", panel_kb())


@router.callback_query(F.data == "adm:close")
async def cb_close(c: CallbackQuery):
    await c.message.delete()
    await c.answer()


def back(to="adm:panel"):
    return [btn("↩️ Назад", to)]


# ---------------- заказы ----------------
@router.callback_query(F.data.regexp(r"^adm:(ok|no):\d+$"))
async def cb_decide(c: CallbackQuery, bot: Bot):
    _, act, oid = c.data.split(":")
    o = db.one("SELECT * FROM orders WHERE id=?", (int(oid),))
    if not o:
        return await c.answer("Заказ не найден", show_alert=True)
    if o["status"] != "pending":
        return await c.answer("Заказ уже обработан", show_alert=True)
    status = "paid" if act == "ok" else "rejected"
    db.ex("UPDATE orders SET status=? WHERE id=?", (status, o["id"]))
    if act == "no" and o["promo"]:  # заказ отклонён — возвращаем покупателю промокод
        db.ex("DELETE FROM promo_uses WHERE code=? AND user_id=?", (o["promo"], o["user_id"]))
        db.ex("UPDATE promocodes SET used_count=MAX(used_count-1,0) WHERE code=?", (o["promo"],))
        db.activate_promo(o["user_id"], o["promo"])
    if act == "ok" and o["tag_id"]:
        db.ex("UPDATE tags SET slots=slots-1 WHERE id=? AND slots IS NOT NULL AND slots>0", (o["tag_id"],))
    who = f"@{c.from_user.username}" if c.from_user.username else str(c.from_user.id)
    try:
        await c.message.edit_text(
            c.message.html_text + f"\n\n{'✅ Подтверждён' if act == 'ok' else '❌ Отклонён'} ({esc(who)})", reply_markup=None)
    except Exception:
        pass
    try:
        if act == "ok":
            await bot.send_message(o["user_id"], t("txt_order_ok", id=o["id"], tag=esc(o["tag"]), term=esc(o["term"])))
            await send_review_prompt(bot, o)
        else:
            await bot.send_message(o["user_id"], t("txt_order_rej", id=o["id"], manager=esc(db.get_setting("manager"))))
    except Exception:
        pass
    await c.answer("Готово")


ST = {"pending": "⏳", "paid": "✅", "rejected": "❌"}
ST_NAME = {"pending": "Ожидают", "paid": "Выполненные", "rejected": "Отклонённые"}


@router.callback_query(F.data == "adm:orders")
async def cb_orders(c: CallbackQuery, state: FSMContext):
    await state.clear()
    cnt = {s: db.one("SELECT COUNT(*) n FROM orders WHERE status=?", (s,))["n"] for s in ST_NAME}
    rows = [[btn(f"{ST[s]} {ST_NAME[s]} ({cnt[s]})", f"adm:ol:{s}")] for s in ST_NAME]
    rows.append([btn("🔎 Найти заказ по номеру", "adm:os")])
    rows.append(back())
    await show(c, "📦 <b>Заказы</b>", kb(rows))


@router.callback_query(F.data.regexp(r"^adm:ol:\w+$"))
async def cb_ol(c: CallbackQuery):
    s = c.data.split(":")[2]
    orders = db.q("SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT 20", (s,))
    rows = [[btn(f"#{o['id']} · {o['tag'].split()[0]} · {o['term']} · {db.money(o['amount'], o['method']).replace(' ', '')}", f"adm:ov:{o['id']}")] for o in orders]
    rows.append(back("adm:orders"))
    await show(c, f"{ST[s]} <b>{ST_NAME[s]}</b> (последние 20)" if orders else "Здесь пока пусто.", kb(rows))


def order_card(o):
    text = (f"<b>Заказ #{o['id']}</b> {ST.get(o['status'], '')}\n👤 {('@' + o['username']) if o['username'] else o['user_id']} (<code>{o['user_id']}</code>)\n"
            f"🎮 ID: {esc(o['game_id'])}\n🏷 {esc(o['tag'])} · {esc(o['term'])}\n"
            f"💵 {db.money(o['amount'], o['method'])} ({db.METHOD_NAME.get(o['method'], o['method'])})"
            + (f"\n🎟 {esc(o['promo'])}" if o['promo'] else "")
            + f"\n🕐 {time.strftime('%d.%m.%Y %H:%M', time.localtime(o['created_at']))}")
    rows = []
    if o["status"] == "pending":
        rows.append([btn("✅ Подтвердить", f"adm:ok:{o['id']}"), btn("❌ Отклонить", f"adm:no:{o['id']}")])
    rows.append([btn("🗑 Удалить заказ", f"adm:od:{o['id']}")])
    rows.append(back(f"adm:ol:{o['status']}"))
    return text, kb(rows)


@router.callback_query(F.data.regexp(r"^adm:ov:\d+$"))
async def cb_ov(c: CallbackQuery):
    o = db.one("SELECT * FROM orders WHERE id=?", (int(c.data.split(":")[2]),))
    if not o:
        return await c.answer("Заказ не найден", show_alert=True)
    text, markup = order_card(o)
    await show(c, text, markup)


@router.callback_query(F.data.regexp(r"^adm:od:\d+$"))
async def cb_od(c: CallbackQuery, state: FSMContext):
    db.ex("DELETE FROM orders WHERE id=?", (int(c.data.split(":")[2]),))
    await c.answer("Заказ удалён")
    await cb_orders(c, state)


@router.callback_query(F.data == "adm:os")
async def cb_os(c: CallbackQuery, state: FSMContext):
    await state.set_state(A.order_search)
    await show(c, "Отправь номер заказа:", kb([back("adm:orders")]))


@router.message(A.order_search, F.text, ~F.text.startswith("/"))
async def got_os(m: Message, state: FSMContext):
    o = db.one("SELECT * FROM orders WHERE id=?", (int(m.text.strip().lstrip("#")),)) if m.text.strip().lstrip("#").isdigit() else None
    if not o:
        return await m.answer("Заказ не найден. Отправь номер ещё раз или нажми «Назад» в панели.")
    await state.clear()
    text, markup = order_card(o)
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data == "adm:stats")
async def cb_stats(c: CallbackQuery):
    users = db.one("SELECT COUNT(*) n FROM users")["n"]
    tot = db.one("SELECT COUNT(*) n FROM orders")["n"]
    pend = db.one("SELECT COUNT(*) n FROM orders WHERE status='pending'")["n"]
    rub = db.one("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='paid' AND method='card'")["s"]
    gold = db.one("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='paid' AND method='gold'")["s"]
    stars = db.one("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='paid' AND method='stars'")["s"]
    boosts = db.one("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='paid' AND method='boost'")["s"]
    promos = db.one("SELECT COALESCE(SUM(used_count),0) s FROM promocodes")["s"]
    text = (f"📊 <b>Статистика</b>\n\n👥 Пользователей: {users}\n📦 Заказов: {tot} (в ожидании: {pend})\n"
            f"💳 Выручка рублями: {fmt(rub)} ₽\n💰 Выручка голдой: {fmt(gold)} г\n"
            f"⭐ Получено звёзд: {fmt(stars)}\n🚀 Получено бустов: {fmt(boosts)}\n🎟 Использований промокодов: {promos}")
    await show(c, text, kb([back()]))


# ---------------- каталог ----------------
@router.callback_query(F.data == "adm:catalog")
async def cb_catalog(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await cat_list(c)


async def cat_list(c):
    rows = [[btn(f"🗂 {r['name']}", f"adm:cat:{r['id']}")] for r in db.q("SELECT * FROM categories ORDER BY position,id")]
    rows.append([btn("➕ Добавить раздел", "adm:cat_add")])
    rows.append(back())
    await show(c, "🗂 <b>Каталог — разделы</b>", kb(rows))


@router.callback_query(F.data == "adm:cat_add")
async def cb_cat_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(A.cat_name)
    await show(c, "Отправь название нового раздела:", kb([back("adm:catalog")]))


@router.message(A.cat_name, F.text, ~F.text.startswith("/"))
async def got_cat(m: Message, state: FSMContext):
    db.ex("INSERT INTO categories(name) VALUES(?)", (m.text.strip(),))
    await state.clear()
    await m.answer("✅ Раздел добавлен.", reply_markup=kb([[btn("🗂 К каталогу", "adm:catalog")]]))


@router.callback_query(F.data.regexp(r"^adm:cat:\d+$"))
async def cb_cat(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await cat_view(c, int(c.data.split(":")[2]))


ADM_PAGE = 30


@router.callback_query(F.data.regexp(r"^adm:catp:\d+:\d+$"))
async def cb_catp(c: CallbackQuery):
    _, _, cid, page = c.data.split(":")
    await cat_view(c, int(cid), int(page))


async def cat_view(c, cid, page=0):
    cat = db.one("SELECT * FROM categories WHERE id=?", (cid,))
    if not cat:
        return await c.answer("Нет такого раздела", show_alert=True)
    tags = db.q("SELECT * FROM tags WHERE category_id=? ORDER BY id", (cid,))
    pages = max(1, (len(tags) + ADM_PAGE - 1) // ADM_PAGE)
    page = max(0, min(page, pages - 1))
    rows = [[btn(("🙈 " if tg["hidden"] else "") + ("🔒 " if tg["closed"] else "") + tg["name"], f"adm:tag:{tg['id']}")] for tg in tags[page * ADM_PAGE:(page + 1) * ADM_PAGE]]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("◀️", f"adm:catp:{cid}:{page - 1}"))
        nav.append(btn(f"{page + 1}/{pages}", "adm:noop"))
        if page < pages - 1:
            nav.append(btn("▶️", f"adm:catp:{cid}:{page + 1}"))
        rows.append(nav)
    rows.append([btn("➕ Добавить тег", f"adm:tag_add:{cid}"), btn("⚡ Быстрая регистрация", f"adm:fast:{cid}")])
    rows.append([btn("✏️ Переименовать", f"adm:cat_ren:{cid}"), btn("🖼 Баннер" + (" ✓" if cat["banner"] else ""), f"adm:eb:cat:{cid}")])
    rows.append([btn("🖼 Баннеры страниц", f"adm:pb:{cid}")])
    rows.append([btn("🗑 Удалить раздел", f"adm:cat_del:{cid}")])
    rows.append(back("adm:catalog"))
    await show(c, f"🗂 <b>{esc(cat['name'])}</b> — тегов: {len(tags)}", kb(rows))


@router.callback_query(F.data == "adm:noop")
async def cb_noop(c: CallbackQuery):
    await c.answer()


@router.callback_query(F.data.regexp(r"^adm:cat_del:\d+$"))
async def cb_cat_del(c: CallbackQuery):
    cid = int(c.data.split(":")[2])
    cat = db.one("SELECT name FROM categories WHERE id=?", (cid,))
    if not cat:
        return await c.answer("Раздел уже удалён", show_alert=True)
    n = db.one("SELECT COUNT(*) n FROM tags WHERE category_id=?", (cid,))["n"]
    await show(c, f"🗑 Удалить раздел <b>{esc(cat['name'])}</b> вместе с тегами ({n}) и их ценами? Это нельзя отменить.",
               kb([[btn("Да, удалить", f"adm:cat_ok:{cid}")], back(f"adm:cat:{cid}")]))


@router.callback_query(F.data.regexp(r"^adm:cat_ok:\d+$"))
async def cb_cat_ok(c: CallbackQuery):
    cid = int(c.data.split(":")[2])
    for row in db.q("SELECT id FROM tags WHERE category_id=?", (cid,)):
        db.ex("DELETE FROM prices WHERE tag_id=?", (row["id"],))
        db.ex("UPDATE promocodes SET tag_id=NULL WHERE tag_id=?", (row["id"],))
    db.ex("DELETE FROM page_banners WHERE category_id=?", (cid,))
    db.ex("DELETE FROM tags WHERE category_id=?", (cid,))
    db.ex("DELETE FROM categories WHERE id=?", (cid,))
    await c.answer("Раздел удалён")
    await cat_list(c)


@router.callback_query(F.data.regexp(r"^adm:tag_add:\d+$"))
async def cb_tag_add(c: CallbackQuery, state: FSMContext):
    cid = int(c.data.split(":")[2])
    await state.set_state(A.tag_new)
    await state.update_data(cid=cid)
    await show(c, "Отправь тег в формате:\n<code>[ТЕГ] | Описание | свободных мест</code>\nместа необязательны. Пример: <code>[MCRW] | Mafiozy Esports | 45</code>\n\n💡 Можно сразу с ценами: ниже строками добавь цены (<code>Неделя | 200 | 400</code>) — см. «⚡ Быстрая регистрация».", kb([back(f"adm:cat:{cid}")]))


PRICE_HELP = ("<code>Срок | цена ₽ | цена голдой | ссылка | зв=50 | буст=3</code>\n"
              "Всё после цены в рублях необязательно и может идти в любом порядке. Голду пропускай как <code>-</code>. "
              "<code>зв=</code> — цена в звёздах, <code>буст=</code> — сколько бустов. Пример:\n"
              "<code>Неделя | 200 | 400 | - | зв=150 | буст=3</code>")
_STARS = re.compile(r"(?i)^(?:зв\w*|stars?|⭐)\s*[=:]?\s*(\d+)$")
_BOOST = re.compile(r"(?i)^(?:буст\w*|boosts?|🚀)\s*[=:]?\s*(\d+)$")


def parse_price(line):
    """'Срок | ₽ | голда | ссылка | зв=50 | буст=3' → dict или None (строка не распознана) / строка-ошибка."""
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    r = dict(term=parts[0], rub=int(parts[1]), gold=int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None,
             url=None, stars=None, boosts=None)
    for x in parts[3:]:
        if x in ("", "-"):
            continue
        if m := _STARS.match(x):
            r["stars"] = int(m.group(1)) or None
        elif m := _BOOST.match(x):
            r["boosts"] = int(m.group(1)) or None
        elif u := links.norm_url(x):
            r["url"] = u
        else:
            return "bad"
    return r


# ---------------- быстрая регистрация тегов ----------------
FAST_HELP = (
    "⚡ <b>Быстрая регистрация</b>\n"
    "Одним сообщением: первая строка — тег, ниже — цены, каждая отдельной строкой.\n\n"
    "<code>[ANBL] | Anable | 45\n"
    "3 дня | 100 | 200\n"
    "Неделя | 200 | 400\n"
    "Месяц | 550 | 1100 | зв=400 | буст=3\n"
    "Год 2650 5300</code>\n\n"
    "• В первой строке описание и места необязательны: можно просто <code>[ANBL]</code>.\n"
    "• Цена: <code>срок | ₽ | голда | ссылка | зв=.. | буст=..</code>; голду можно пропустить (<code>-</code>) или вообще не писать. "
    "Работает и без «|»: <code>Неделя 200 400</code>.\n"
    "• Хочешь несколько тегов сразу — раздели их пустой строкой.\n"
    "• Если тег с таким названием в разделе уже есть, цены добавятся к нему."
)



def parse_price_loose(line):
    """Как parse_price, но понимает и строки без «|»: 'Неделя 200 400', 'Месяц - 550₽'. None — не цена."""
    line = line.strip()
    if "|" in line:
        return parse_price(line)
    extras = {}
    for kind, rx in (("stars", re.compile(r"(?i)(?:зв\w*|stars?|⭐)\s*[=:]?\s*(\d+)")),
                     ("boosts", re.compile(r"(?i)(?:буст\w*|boosts?|🚀)\s*[=:]?\s*(\d+)"))):
        if m := rx.search(line):
            extras[kind] = int(m.group(1)) or None
            line = (line[:m.start()] + " " + line[m.end():]).strip()
    line = re.sub(r"(?:(?<=\s)|^)[\-–—](?=\s|$)", " ", line).strip()  # одиночные «-» — разделители/пропуски
    m = re.match(r"^(?P<term>(?:\d+\s*)?[^\d\s][^\d]*?)[\s:=\-–—]*(?P<rest>\d[\d\s₽рРруб.гоGg/,]*)$", line)
    if not m:
        return None
    term = m.group("term").strip(" :=-–—")
    nums = re.findall(r"\d+", m.group("rest"))
    if not term or not nums:
        return None
    return dict(term=term, rub=int(nums[0]), gold=int(nums[1]) if len(nums) > 1 else None, url=None,
                stars=extras.get("stars"), boosts=extras.get("boosts"))


def fast_register(cid, text):
    """Разбирает текст на блоки (тег + цены) и записывает в БД. Возвращает (отчёт-строки, ошибки)."""
    report, errors = [], []
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        head = [p.strip() for p in lines[0].split("|")]
        name = head[0]
        if not name:
            errors.append(f"Пустое название тега: <code>{esc(lines[0])}</code>")
            continue
        prices, bad = [], []
        for ln in lines[1:]:
            r = parse_price_loose(ln)
            if r is None or r == "bad":
                bad.append(ln)
            else:
                prices.append(r)
        if not prices:
            errors.append(f"Тег <b>{esc(name)}</b>: не нашёл ни одной цены — пропущен.")
            continue
        slots = int(head[2]) if len(head) > 2 and head[2].isdigit() else None
        desc = head[1] if len(head) > 1 else ""
        tg = db.one("SELECT * FROM tags WHERE category_id=? AND name=?", (cid, name))
        if tg:
            tid, status = tg["id"], "дополнен"
        else:
            tid = db.ex("INSERT INTO tags(category_id,name,description,slots) VALUES(?,?,?,?)", (cid, name, desc, slots))
            status = "создан"
        for r in prices:
            db.ex("INSERT INTO prices(tag_id,term,price_rub,price_gold,url,price_stars,price_boosts) VALUES(?,?,?,?,?,?,?)",
                  (tid, r["term"], r["rub"], r["gold"], r["url"], r["stars"], r["boosts"]))
        report.append(f"✅ <b>{esc(name)}</b> — {status}, цен: {len(prices)}")
        if bad:
            errors.append(f"В теге <b>{esc(name)}</b> пропущены строки: " + "; ".join(f"<code>{esc(b)}</code>" for b in bad))
    return report, errors


@router.callback_query(F.data.regexp(r"^adm:fast:\d+$"))
async def cb_fast(c: CallbackQuery, state: FSMContext):
    cid = int(c.data.split(":")[2])
    await state.set_state(A.fast_tag)
    await state.update_data(cid=cid)
    await show(c, FAST_HELP, kb([back(f"adm:cat:{cid}")]))


@router.message(A.fast_tag, F.text, ~F.text.startswith("/"))
async def got_fast(m: Message, state: FSMContext):
    d = await state.get_data()
    report, errors = fast_register(d["cid"], m.text)
    if not report:
        return await m.answer("Не получилось ничего зарегистрировать.\n" + "\n".join(errors) + "\n\n" + FAST_HELP)
    # остаёмся в режиме — можно сразу слать следующий тег
    text = "\n".join(report)
    if errors:
        text += "\n\n⚠️ " + "\n".join(errors)
    await m.answer(text + "\n\nМожешь прислать следующий тег или нажать «Готово».",
                   reply_markup=kb([[btn("✔️ Готово", f"adm:cat:{d['cid']}")]]))



@router.message(A.tag_new, F.text, ~F.text.startswith("/"))
async def got_tag(m: Message, state: FSMContext):
    d = await state.get_data()
    if len([ln for ln in m.text.splitlines() if ln.strip()]) > 1:  # тег + цены в одном сообщении
        report, errors = fast_register(d["cid"], m.text)
        if report:
            await state.clear()
            text = "\n".join(report) + (("\n\n⚠️ " + "\n".join(errors)) if errors else "")
            return await m.answer(text, reply_markup=kb([[btn("🗂 К разделу", f"adm:cat:{d['cid']}")]]))
        return await m.answer("\n".join(errors) + "\n\n" + FAST_HELP)
    parts = [p.strip() for p in m.text.split("|")]
    if not parts[0]:
        return await m.answer("Название тега не может быть пустым. Отправь ещё раз: НАЗВАНИЕ | описание | места")
    slots = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    tid = db.ex("INSERT INTO tags(category_id,name,description,slots) VALUES(?,?,?,?)",
                (d["cid"], parts[0], parts[1] if len(parts) > 1 else "", slots))
    await state.set_state(A.prices_new)
    await state.update_data(tid=tid)
    await m.answer(
        "Теперь цены — по одной строке:\n" + PRICE_HELP + "\n"
        "Ссылка — если оплата идёт через сайт. Ещё примеры:\n<code>Неделя | 200 | 400\nМесяц | 550 | - | https://site.ru/pay</code>")


@router.message(A.prices_new, F.text, ~F.text.startswith("/"))
async def got_prices(m: Message, state: FSMContext):
    d = await state.get_data()
    n = 0
    bad = []
    for line in m.text.splitlines():
        r = parse_price(line)
        if r is None:
            continue
        if r == "bad":
            bad.append(line)
            continue
        db.ex("INSERT INTO prices(tag_id,term,price_rub,price_gold,url,price_stars,price_boosts) VALUES(?,?,?,?,?,?,?)",
              (d["tid"], r["term"], r["rub"], r["gold"], r["url"], r["stars"], r["boosts"]))
        n += 1
    if not n:
        return await m.answer("Не распознал ни одной строки. Формат:\n" + PRICE_HELP)
    await state.clear()
    if bad:
        await m.answer("⚠️ Эти строки пропущены — не понял ссылку или параметр:\n" + "\n".join(f"<code>{esc(b)}</code>" for b in bad))
    await m.answer(f"✅ Добавлено цен: {n}.", reply_markup=kb([[btn("🏷 К тегу", f"adm:tag:{d['tid']}")]]))


@router.callback_query(F.data.regexp(r"^adm:tag:\d+$"))
async def cb_tag(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await tag_view(c, int(c.data.split(":")[2]))


async def tag_view(c, tid):
    t = db.one("SELECT * FROM tags WHERE id=?", (tid,))
    if not t:
        return await c.answer("Нет такого тега", show_alert=True)
    prices = db.q("SELECT * FROM prices WHERE tag_id=? ORDER BY price_rub", (tid,))
    rows = [[btn(f"{p['term']} — {fmt(p['price_rub'])}₽" + (f" / {fmt(p['price_gold'])}г" if p['price_gold'] else "") + (f" / {fmt(p['price_stars'])}⭐" if p['price_stars'] else "") + (f" / {p['price_boosts']}🚀" if p['price_boosts'] else "") + (" 🔗" if p['url'] else "") + (" 🔒" if p['closed'] else ""), f"adm:pr:{p['id']}")] for p in prices]
    rows.append([btn("➕ Добавить цены", f"adm:pr_add:{tid}"), btn("🔢 Места", f"adm:slots:{tid}")])
    rows.append([btn("✏️ Название/описание", f"adm:tag_ed:{tid}"), btn("🖼 Баннер" + (" ✓" if t["banner"] else ""), f"adm:eb:tag:{tid}")])
    rows.append([btn("🔓 Открыть покупку" if t["closed"] else "🔒 Закрыть покупку", f"adm:tag_c:{tid}")])
    rows.append([btn("👁 Показать пользователям" if t["hidden"] else "🙈 Скрыть от пользователей", f"adm:tag_h:{tid}")])
    rows.append([btn("🗑 Удалить тег", f"adm:tag_del:{tid}")])
    rows.append(back(f"adm:cat:{t['category_id']}"))
    await show(c, f"🏷 <b>{esc(t['name'])}</b> {esc(t['description'])}\nСвободных мест: {t['slots'] if t['slots'] is not None else '∞'}\n{'🔒 Покупка закрыта (тег виден, купить нельзя)' + chr(10) if t['closed'] else ''}{'🙈 Скрыт от пользователей' + chr(10) if t['hidden'] else ''}Нажми на цену, чтобы изменить или удалить её.", kb(rows))


@router.callback_query(F.data.regexp(r"^adm:pr_add:\d+$"))
async def cb_pr_add(c: CallbackQuery, state: FSMContext):
    tid = int(c.data.split(":")[2])
    await state.set_state(A.prices_new)
    await state.update_data(tid=tid)
    await show(c, "Отправь цены, по строке:\n" + PRICE_HELP, kb([back(f"adm:tag:{tid}")]))


@router.callback_query(F.data.regexp(r"^adm:pr_del:\d+$"))
async def cb_pr_del(c: CallbackQuery, state: FSMContext):
    p = db.one("SELECT * FROM prices WHERE id=?", (int(c.data.split(":")[2]),))
    if p:
        db.ex("DELETE FROM prices WHERE id=?", (p["id"],))
        await tag_view(c, p["tag_id"])


@router.callback_query(F.data.regexp(r"^adm:tag_del:\d+$"))
async def cb_tag_del(c: CallbackQuery):
    tg = db.one("SELECT * FROM tags WHERE id=?", (int(c.data.split(":")[2]),))
    if not tg:
        return await c.answer("Тег уже удалён", show_alert=True)
    await show(c, f"🗑 Удалить тег <b>{esc(tg['name'])}</b> и все его цены? Это нельзя отменить.",
               kb([[btn("Да, удалить", f"adm:tag_ok:{tg['id']}")], back(f"adm:tag:{tg['id']}")]))


@router.callback_query(F.data.regexp(r"^adm:tag_ok:\d+$"))
async def cb_tag_ok(c: CallbackQuery):
    tg = db.one("SELECT * FROM tags WHERE id=?", (int(c.data.split(":")[2]),))
    if tg:
        db.ex("DELETE FROM prices WHERE tag_id=?", (tg["id"],))
        db.ex("UPDATE promocodes SET tag_id=NULL WHERE tag_id=?", (tg["id"],))
        db.ex("DELETE FROM tags WHERE id=?", (tg["id"],))
        await c.answer("Тег удалён")
        await cat_view(c, tg["category_id"])


# ---------------- промокоды ----------------
@router.callback_query(F.data == "adm:promos")
async def cb_promos(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await promos_list(c)


def promo_target(p):
    """Название тега, к которому привязан промокод (None — на все теги)."""
    if p["tag_id"] is None:
        return None
    tg = db.one("SELECT name FROM tags WHERE id=?", (p["tag_id"],))
    return tg["name"] if tg else "тег удалён"


def find_tag(s):
    """Тег по названию (или #id). Возвращает (строка тега, текст ошибки)."""
    s = s.strip()
    if re.fullmatch(r"#\d+", s):
        tg = db.one("SELECT * FROM tags WHERE id=?", (int(s[1:]),))
        return (tg, None) if tg else (None, f"Тега с id {s[1:]} нет.")
    found = db.q("SELECT * FROM tags WHERE LOWER(name)=LOWER(?)", (s,))
    if not found:
        return None, f"Тег «{esc(s)}» не найден. Название должно совпадать с названием тега в каталоге."
    if len(found) > 1:
        ids = ", ".join(f"#{x['id']}" for x in found)
        return None, f"Тегов с таким названием несколько ({ids}) — укажи точный: <code>#id</code>."
    return found[0], None


async def promos_list(c):
    rows = []
    for p in db.q("SELECT * FROM promocodes ORDER BY rowid DESC LIMIT 30"):
        lim = p["max_uses"] or "∞"
        tg = promo_target(p)
        rows.append([btn(f"{'🟢' if p['active'] else '⚪️'} {p['code']} −{p['percent']}%{' · ' + tg if tg else ''} ({p['used_count']}/{lim})", f"adm:promo:{p['code']}")])
    rows.append([btn("➕ Создать промокод", "adm:promo_add")])
    rows.append(back())
    await show(c, "🎟 <b>Промокоды</b>\nСкидка действует на одну покупку — на все товары или на конкретный тег.", kb(rows))


@router.callback_query(F.data == "adm:promo_add")
async def cb_promo_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(A.promo_new)
    await show(c, "Отправь: <code>КОД | скидка % | лимит использований | тег</code>\nЛимит 0 = без ограничений. Тег — по желанию: без него скидка на все товары.\nПримеры:\n<code>SALE20 | 20 | 50</code> — на все теги\n<code>SALE20 | 20 | 50 | [ANBL]</code> — только на тег [ANBL]\n(если названий тега несколько — укажи <code>#id</code>)", kb([back("adm:promos")]))


@router.message(A.promo_new, F.text, ~F.text.startswith("/"))
async def got_promo(m: Message, state: FSMContext):
    parts = [p.strip() for p in m.text.split("|")]
    if len(parts) < 2 or not parts[1].isdigit() or not 1 <= int(parts[1]) <= 100:
        return await m.answer("Формат: <code>КОД | скидка % (1–100) | лимит</code>")
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,20}", parts[0]):
        return await m.answer("Код — только латинские буквы, цифры, «-» и «_», 2–20 символов. Пример: <code>SALE20</code>")
    limit = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    tag = None
    if len(parts) > 3 and parts[3]:
        tag, err = find_tag(parts[3])
        if err:
            return await m.answer(err)
    db.ex("INSERT OR REPLACE INTO promocodes(code,percent,max_uses,used_count,active,tag_id) VALUES(?,?,?,0,1,?)",
          (parts[0].upper(), int(parts[1]), limit, tag["id"] if tag else None))
    await state.clear()
    await m.answer(f"✅ Промокод <b>{esc(parts[0].upper())}</b> создан: −{parts[1]}% "
                   + (f"на тег <b>{esc(tag['name'])}</b>." if tag else "на все теги."),
                   reply_markup=kb([[btn("🎟 К промокодам", "adm:promos")]]))


@router.callback_query(F.data.startswith("adm:promo:"))
async def cb_promo(c: CallbackQuery):
    await promo_view(c, c.data.split(":", 2)[2])


async def promo_view(c, code):
    p = db.one("SELECT * FROM promocodes WHERE code=?", (code,))
    if not p:
        return await c.answer("Не найден", show_alert=True)
    tg = promo_target(p)
    text = (f"🎟 <b>{esc(p['code'])}</b>\nСкидка: {p['percent']}%\nДействует на: {('тег ' + esc(tg)) if tg else 'все теги'}\nИспользован: {p['used_count']} / {p['max_uses'] or '∞'}\n"
            f"Статус: {'включён' if p['active'] else 'выключен'}")
    await show(c, text, kb([
        [btn("🔁 Вкл/выкл", f"adm:promo_t:{code}"), btn("🗑 Удалить", f"adm:promo_d:{code}")],
        [btn("🏷 Привязать к тегу", f"adm:promo_tg:{code}")] + ([btn("🌐 На все теги", f"adm:promo_all:{code}")] if p["tag_id"] is not None else []),
        back("adm:promos"),
    ]))


@router.callback_query(F.data.startswith("adm:promo_tg:"))
async def cb_promo_tg(c: CallbackQuery, state: FSMContext):
    code = c.data.split(":", 2)[2]
    await state.set_state(A.promo_tag)
    await state.update_data(promo_code=code)
    await show(c, "Отправь название тега (например <code>[ANBL]</code>) или <code>#id</code>, если названий несколько.", kb([back(f"adm:promo:{code}")]))


@router.message(A.promo_tag, F.text, ~F.text.startswith("/"))
async def got_promo_tag(m: Message, state: FSMContext):
    code = (await state.get_data()).get("promo_code")
    tag, err = find_tag(m.text)
    if err:
        return await m.answer(err)
    db.ex("UPDATE promocodes SET tag_id=? WHERE code=?", (tag["id"], code))
    await state.clear()
    await m.answer(f"✅ Промокод <b>{esc(code)}</b> теперь действует только на тег <b>{esc(tag['name'])}</b>.",
                   reply_markup=kb([[btn("🎟 К промокоду", f"adm:promo:{code}")]]))


@router.callback_query(F.data.startswith("adm:promo_all:"))
async def cb_promo_all(c: CallbackQuery):
    code = c.data.split(":", 2)[2]
    db.ex("UPDATE promocodes SET tag_id=NULL WHERE code=?", (code,))
    await c.answer("Теперь на все теги")
    await promo_view(c, code)


@router.callback_query(F.data.startswith("adm:promo_t:"))
async def cb_promo_t(c: CallbackQuery):
    code = c.data.split(":", 2)[2]
    db.ex("UPDATE promocodes SET active=1-active WHERE code=?", (code,))
    await promo_view(c, code)


@router.callback_query(F.data.startswith("adm:promo_d:"))
async def cb_promo_d(c: CallbackQuery, state: FSMContext):
    db.ex("DELETE FROM promocodes WHERE code=?", (c.data.split(":", 2)[2],))
    await c.answer("Удалён")
    await promos_list(c)


# ---------------- админы ----------------
@router.callback_query(F.data == "adm:admins")
async def cb_admins(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await admins_list(c)


async def admins_list(c):
    rows = []
    for a in db.q("SELECT * FROM admins"):
        u = db.one("SELECT username FROM users WHERE user_id=?", (a["user_id"],))
        label = f"{a['user_id']}" + (f" (@{u['username']})" if u and u["username"] else "")
        rows.append([btn(f"🗑 {label}", f"adm:adm_d:{a['user_id']}")])
    rows.append([btn("➕ Добавить админа", "adm:adm_add")])
    rows.append(back())
    await show(c, "👥 <b>Администраторы</b>\nНажми на админа, чтобы удалить его.", kb(rows))


@router.callback_query(F.data == "adm:adm_add")
async def cb_adm_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(A.admin_add)
    await show(c, "Отправь Telegram ID нового админа (число) или @username (человек должен был хотя бы раз нажать /start в боте):", kb([back("adm:admins")]))


@router.message(A.admin_add, F.text, ~F.text.startswith("/"))
async def got_admin(m: Message, state: FSMContext, bot: Bot):
    t = m.text.strip()
    uid = None
    if t.isdigit():
        uid = int(t)
    else:
        r = db.one("SELECT user_id FROM users WHERE lower(username)=lower(?)", (t.lstrip("@"),))
        uid = r["user_id"] if r else None
    if not uid:
        return await m.answer("Пользователь не найден. Отправь числовой ID или попроси его нажать /start в боте.")
    db.ex("INSERT OR IGNORE INTO admins VALUES(?,?,?)", (uid, m.from_user.id, int(time.time())))
    await state.clear()
    await m.answer(f"✅ Админ <code>{uid}</code> добавлен.", reply_markup=kb([[btn("👥 К админам", "adm:admins")]]))
    try:
        await bot.send_message(uid, "🛠 Тебя добавили администратором бота. Команда: /admin")
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:adm_d:"))
async def cb_adm_d(c: CallbackQuery, state: FSMContext):
    uid = int(c.data.split(":")[2])
    if uid == c.from_user.id:
        return await c.answer("Себя удалить нельзя", show_alert=True)
    if len(db.admin_ids()) <= 1:
        return await c.answer("Нужен хотя бы один админ", show_alert=True)
    db.ex("DELETE FROM admins WHERE user_id=?", (uid,))
    await c.answer("Удалён")
    await admins_list(c)


# ---------------- настройки ----------------
@router.callback_query(F.data == "adm:settings")
async def cb_settings(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [[btn(name, f"adm:set:{k}")] for k, name in SETTING_NAMES.items()]
    rows.append(back())
    await show(c, "⚙️ <b>Настройки</b>\nСвязи и реквизиты. Все тексты и кнопки — в «✏️ Тексты бота».", kb(rows))


@router.callback_query(F.data.startswith("adm:set:"))
async def cb_set(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":")[2]
    if key not in SETTING_NAMES:
        return await c.answer("Эти тексты теперь в «✏️ Тексты бота»", show_alert=True)
    await state.set_state(A.setting)
    await state.update_data(key=key)
    cur = db.get_setting(key)
    hint = ""
    await c.message.answer(f"<b>{SETTING_NAMES[key]}</b>\nТекущее значение:\n<pre>{esc(cur)}</pre>\nОтправь новое значение (HTML-теги допустимы).{hint}",
                           reply_markup=kb([back("adm:settings")]))
    await c.answer()


@router.message(A.setting, F.text, ~F.text.startswith("/"))
async def got_setting(m: Message, state: FSMContext):
    d = await state.get_data()
    key = d["key"]
    raw = (m.text or "").strip()
    if key in LINK_SETTINGS:
        if raw in ("-", "—", "0") and key in ("reviews_url", "channel_url", "reviews_chat", "stars_contact", "boost_contact"):
            value = ""
        else:
            value = LINK_SETTINGS[key][0](raw)
            if not value:
                return await m.answer(f"❌ Не похоже на корректное значение.\n{LINK_SETTINGS[key][1]}\nПопробуй ещё раз.")
    else:
        value = raw  # реквизиты и прочее — обычный текст, без HTML
    db.set_setting(key, value)
    await state.clear()
    await m.answer("✅ Сохранено.", reply_markup=kb([[btn("⚙️ К настройкам", "adm:settings")]]))


# ---------------- рассылка ----------------
@router.callback_query(F.data == "adm:bc")
async def cb_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(A.broadcast)
    await show(c, "📢 Отправь текст рассылки (получат все пользователи бота):", kb([back()]))


@router.message(A.broadcast, F.text, ~F.text.startswith("/"))
async def got_bc(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    ok = bad = 0
    await m.answer("📢 Рассылка запущена…")
    for u in db.q("SELECT user_id FROM users WHERE COALESCE(banned,0)=0"):
        for attempt in range(3):
            try:
                await bot.send_message(u["user_id"], m.html_text)
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                bad += 1
                break
        await asyncio.sleep(0.05)  # лимит Telegram ~30 сообщений/сек
    await m.answer(f"📢 Рассылка завершена. Доставлено: {ok}, ошибок: {bad}.", reply_markup=kb([back()]))


# ---------------- места ----------------
@router.callback_query(F.data.regexp(r"^adm:slots:\d+$"))
async def cb_slots(c: CallbackQuery, state: FSMContext):
    tid = int(c.data.split(":")[2])
    await state.set_state(A.slots)
    await state.update_data(tid=tid)
    await show(c, "Отправь число свободных мест или <code>-</code> для «без ограничений»:", kb([back(f"adm:tag:{tid}")]))


@router.message(A.slots, F.text, ~F.text.startswith("/"))
async def got_slots(m: Message, state: FSMContext):
    d = await state.get_data()
    t = m.text.strip()
    if t == "-":
        db.ex("UPDATE tags SET slots=NULL WHERE id=?", (d["tid"],))
    elif t.isdigit():
        db.ex("UPDATE tags SET slots=? WHERE id=?", (int(t), d["tid"]))
    else:
        return await m.answer("Нужно число или <code>-</code>")
    await state.clear()
    await m.answer("✅ Сохранено.", reply_markup=kb([[btn("🏷 К тегу", f"adm:tag:{d['tid']}")]]))


# ---------------- ответ на вопрос ----------------
@router.callback_query(F.data.regexp(r"^adm:reply:\d+$"))
async def cb_reply(c: CallbackQuery, state: FSMContext):
    await state.set_state(A.reply)
    await state.update_data(uid=int(c.data.split(":")[2]))
    await c.message.answer("Напиши ответ — я отправлю его пользователю:")
    await c.answer()


@router.message(A.reply, F.text, ~F.text.startswith("/"))
async def got_reply(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    try:
        await bot.send_message(d["uid"], t("txt_answer") + f"\n\n{m.html_text}")
        await m.answer("✅ Ответ отправлен.")
    except Exception:
        await m.answer("❌ Не удалось отправить (пользователь заблокировал бота?).")


# ---------------- баннеры ----------------
BANNERS = {
    "main": "Главное меню",
    "catalog": "Разделы каталога",
    "tags": "Список тегов",
    "terms": "Выбор срока",
    "game_id": "Ввод игрового ID",
    "order": "Экран заказа",
    "orders": "История покупок",
    "help": "Поддержка",
    "rules": "Условия",
    "promo": "Промокод",
}


@router.callback_query(F.data == "adm:banners")
async def cb_banners(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [[btn(("🖼 " if db.get_setting("banner_" + k) else "▫️ ") + name, f"adm:bn:{k}")] for k, name in BANNERS.items()]
    rows.append(back())
    await show(c, "🖼 <b>Баннеры</b>\nКартинка показывается над текстом экрана. 🖼 — баннер задан.\nБаннеры отдельных разделов и тегов — в «Каталог» → раздел/тег → «🖼 Баннер».", kb(rows))


@router.callback_query(F.data.regexp(r"^adm:bn:\w+$"))
async def cb_bn(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":")[2]
    await state.set_state(A.banner)
    await state.update_data(key=key)
    rows = [[btn("🗑 Убрать баннер", f"adm:bn_del:{key}")], back("adm:banners")]
    await c.message.answer(f"Отправь картинку (как фото) для экрана «{BANNERS[key]}»:", reply_markup=kb(rows))
    await c.answer()


@router.message(A.banner, F.photo)
async def got_banner(m: Message, state: FSMContext):
    d = await state.get_data()
    db.set_setting("banner_" + d["key"], m.photo[-1].file_id)
    await state.clear()
    await m.answer("✅ Баннер сохранён.", reply_markup=kb([[btn("🖼 К баннерам", "adm:banners")]]))


@router.message(A.banner)
async def got_banner_bad(m: Message):
    await m.answer("Нужно отправить именно картинку (фото).")


@router.callback_query(F.data.regexp(r"^adm:bn_del:\w+$"))
async def cb_bn_del(c: CallbackQuery, state: FSMContext):
    db.set_setting("banner_" + c.data.split(":")[2], "")
    await state.clear()
    await c.answer("Баннер убран")


# ---------------- раздел/тег: правка ----------------
@router.callback_query(F.data.regexp(r"^adm:cat_ren:\d+$"))
async def cb_cat_ren(c: CallbackQuery, state: FSMContext):
    cid = int(c.data.split(":")[2])
    await state.set_state(A.cat_rename)
    await state.update_data(cid=cid)
    await show(c, "Отправь новое название раздела:", kb([back(f"adm:cat:{cid}")]))


@router.message(A.cat_rename, F.text, ~F.text.startswith("/"))
async def got_cat_ren(m: Message, state: FSMContext):
    d = await state.get_data()
    db.ex("UPDATE categories SET name=? WHERE id=?", (m.text.strip(), d["cid"]))
    await state.clear()
    await m.answer("✅ Переименовано.", reply_markup=kb([[btn("🗂 К разделу", f"adm:cat:{d['cid']}")]]))


@router.callback_query(F.data.regexp(r"^adm:tag_ed:\d+$"))
async def cb_tag_ed(c: CallbackQuery, state: FSMContext):
    tid = int(c.data.split(":")[2])
    await state.set_state(A.tag_edit)
    await state.update_data(tid=tid)
    await show(c, "Отправь: <code>[ТЕГ] | Описание</code>", kb([back(f"adm:tag:{tid}")]))


@router.message(A.tag_edit, F.text, ~F.text.startswith("/"))
async def got_tag_ed(m: Message, state: FSMContext):
    d = await state.get_data()
    name, _, desc = m.text.partition("|")
    db.ex("UPDATE tags SET name=?, description=? WHERE id=?", (name.strip(), desc.strip(), d["tid"]))
    await state.clear()
    await m.answer("✅ Сохранено.", reply_markup=kb([[btn("🏷 К тегу", f"adm:tag:{d['tid']}")]]))


@router.callback_query(F.data.regexp(r"^adm:tag_c:\d+$"))
async def cb_tag_c(c: CallbackQuery):
    tid = int(c.data.split(":")[2])
    db.ex("UPDATE tags SET closed=1-COALESCE(closed,0) WHERE id=?", (tid,))
    r = db.one("SELECT closed FROM tags WHERE id=?", (tid,))
    await c.answer("🔒 Покупка закрыта" if r and r["closed"] else "🔓 Покупка открыта")
    await tag_view(c, tid)


@router.callback_query(F.data.regexp(r"^adm:pr_c:\d+$"))
async def cb_pr_c(c: CallbackQuery, state: FSMContext):
    pid = int(c.data.split(":")[2])
    db.ex("UPDATE prices SET closed=1-COALESCE(closed,0) WHERE id=?", (pid,))
    r = db.one("SELECT closed FROM prices WHERE id=?", (pid,))
    await c.answer("🔒 Срок закрыт" if r and r["closed"] else "🔓 Срок открыт")
    await show_price(c, pid)


@router.callback_query(F.data.regexp(r"^adm:tag_h:\d+$"))
async def cb_tag_h(c: CallbackQuery):
    tid = int(c.data.split(":")[2])
    db.ex("UPDATE tags SET hidden=1-COALESCE(hidden,0) WHERE id=?", (tid,))
    await tag_view(c, tid)


# ---------------- цена: просмотр / правка ----------------
@router.callback_query(F.data.regexp(r"^adm:pr:\d+$"))
async def cb_pr(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_price(c, int(c.data.split(":")[2]))


async def show_price(c, pid):
    p = db.one("SELECT * FROM prices WHERE id=?", (pid,))
    if not p:
        return await c.answer("Не найдено", show_alert=True)
    text = (f"💲 <b>{esc(p['term'])}</b>\nРубли: {fmt(p['price_rub'])}\nГолда: {fmt(p['price_gold']) if p['price_gold'] else '—'}\n"
            f"⭐ Звёзды: {fmt(p['price_stars']) if p['price_stars'] else '—'}\n"
            f"🚀 Бусты: {p['price_boosts'] or '—'}\n"
            f"Ссылка на оплату: {esc(p['url']) if p['url'] else '—'}"
            + ("\n🔒 Этот срок закрыт для покупки" if p["closed"] else ""))
    await show(c, text, kb([[btn("🔓 Открыть срок" if p["closed"] else "🔒 Закрыть срок", f"adm:pr_c:{p['id']}")],
                            [btn("✏️ Изменить", f"adm:pr_ed:{p['id']}"), btn("🗑 Удалить", f"adm:pr_del:{p['id']}")],
                            back(f"adm:tag:{p['tag_id']}")]))


@router.callback_query(F.data.regexp(r"^adm:pr_ed:\d+$"))
async def cb_pr_ed(c: CallbackQuery, state: FSMContext):
    pid = int(c.data.split(":")[2])
    await state.set_state(A.price_edit)
    await state.update_data(pid=pid)
    await show(c, "Отправь новую цену одной строкой (заменяются все поля):\n" + PRICE_HELP,
               kb([back(f"adm:pr:{pid}")]))


@router.message(A.price_edit, F.text, ~F.text.startswith("/"))
async def got_pr_ed(m: Message, state: FSMContext):
    d = await state.get_data()
    r = parse_price(m.text.splitlines()[0] if m.text.strip() else "")
    if r is None:
        return await m.answer("Формат:\n" + PRICE_HELP)
    if r == "bad":
        return await m.answer("❌ Не понял ссылку или параметр. Ссылка: https://site.ru/pay, звёзды: <code>зв=50</code>, бусты: <code>буст=3</code>.")
    db.ex("UPDATE prices SET term=?, price_rub=?, price_gold=?, url=?, price_stars=?, price_boosts=? WHERE id=?",
          (r["term"], r["rub"], r["gold"], r["url"], r["stars"], r["boosts"], d["pid"]))
    await state.clear()
    await m.answer("✅ Цена обновлена.", reply_markup=kb([[btn("💲 К цене", f"adm:pr:{d['pid']}")]]))


# ---------------- баннеры раздела / тега ----------------
@router.callback_query(F.data.regexp(r"^adm:eb:(cat|tag):\d+$"))
async def cb_eb(c: CallbackQuery, state: FSMContext):
    _, _, kind, eid = c.data.split(":")
    await state.set_state(A.ent_banner)
    await state.update_data(kind=kind, eid=int(eid))
    back_to = f"adm:cat:{eid}" if kind == "cat" else f"adm:tag:{eid}"
    await show(c, "Отправь картинку (как фото) — она будет показываться на этой странице в боте."
                  + ("\nБаннер тега приоритетнее баннера раздела." if kind == "tag" else ""),
               kb([[btn("🗑 Убрать баннер", f"adm:ebd:{kind}:{eid}")], back(back_to)]))


@router.message(A.ent_banner, F.photo)
async def got_eb(m: Message, state: FSMContext):
    d = await state.get_data()
    tbl = "categories" if d["kind"] == "cat" else "tags"
    db.ex(f"UPDATE {tbl} SET banner=? WHERE id=?", (m.photo[-1].file_id, d["eid"]))
    await state.clear()
    to = f"adm:cat:{d['eid']}" if d["kind"] == "cat" else f"adm:tag:{d['eid']}"
    await m.answer("✅ Баннер сохранён.", reply_markup=kb([[btn("↩️ Назад", to)]]))


@router.message(A.ent_banner)
async def got_eb_bad(m: Message):
    await m.answer("Нужно отправить именно картинку (фото).")


@router.callback_query(F.data.regexp(r"^adm:ebd:(cat|tag):\d+$"))
async def cb_ebd(c: CallbackQuery, state: FSMContext):
    _, _, kind, eid = c.data.split(":")
    db.ex(f"UPDATE {'categories' if kind == 'cat' else 'tags'} SET banner=NULL WHERE id=?", (int(eid),))
    await state.clear()
    await c.answer("Баннер убран")
    await (cat_view(c, int(eid)) if kind == "cat" else tag_view(c, int(eid)))


# ---------------- баннеры страниц списка тегов ----------------
def _pages(cid):
    return max(1, (db.visible_tag_count(cid) + db.USER_PAGE - 1) // db.USER_PAGE)


@router.callback_query(F.data.regexp(r"^adm:pb:\d+$"))
async def cb_pb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await pb_view(c, int(c.data.split(":")[2]))


async def pb_view(c, cid):
    cat = db.one("SELECT name FROM categories WHERE id=?", (cid,))
    if not cat:
        return await c.answer("Раздел не найден", show_alert=True)
    n = _pages(cid)
    rows, line = [], []
    for p in range(n):
        line.append(btn(("🖼 " if db.page_banner(cid, p) else "▫️ ") + f"Страница {p + 1}", f"adm:pbs:{cid}:{p}"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append(back(f"adm:cat:{cid}"))
    await show(c, f"🖼 <b>Баннеры страниц — {esc(cat['name'])}</b>\n"
                  f"Страниц у покупателя: {n} (по {db.USER_PAGE} тегов). Новая страница появляется сама, "
                  "когда тегов становится больше. Баннер «Выбери тэг» собирается автоматически из тегов страницы. "
                  "🖼 — свой баннер задан вручную (он главнее автоматического); «Убрать баннер» вернёт автоматический.", kb(rows))


@router.callback_query(F.data.regexp(r"^adm:pbs:\d+:\d+$"))
async def cb_pbs(c: CallbackQuery, state: FSMContext):
    _, _, cid, p = c.data.split(":")
    await state.set_state(A.page_banner)
    await state.update_data(cid=int(cid), page=int(p))
    await show(c, f"Отправь картинку (как фото) для страницы {int(p) + 1} списка тегов.",
               kb([[btn("🗑 Убрать баннер", f"adm:pbd:{cid}:{p}")], back(f"adm:pb:{cid}")]))


@router.message(A.page_banner, F.photo)
async def got_pb(m: Message, state: FSMContext):
    d = await state.get_data()
    db.ex("INSERT OR REPLACE INTO page_banners VALUES(?,?,?)", (d["cid"], d["page"], m.photo[-1].file_id))
    await state.clear()
    await m.answer("✅ Баннер страницы сохранён.", reply_markup=kb([[btn("↩️ К страницам", f"adm:pb:{d['cid']}")]]))


@router.message(A.page_banner)
async def got_pb_bad(m: Message):
    await m.answer("Нужно отправить именно картинку (фото).")


@router.callback_query(F.data.regexp(r"^adm:pbd:\d+:\d+$"))
async def cb_pbd(c: CallbackQuery, state: FSMContext):
    _, _, cid, p = c.data.split(":")
    db.ex("DELETE FROM page_banners WHERE category_id=? AND page=?", (int(cid), int(p)))
    await state.clear()
    await c.answer("Баннер убран")
    await pb_view(c, int(cid))


# ---------------- пользователи ----------------
@router.callback_query(F.data == "adm:users")
async def cb_users(c: CallbackQuery, state: FSMContext):
    await state.clear()
    n = db.one("SELECT COUNT(*) n FROM users")["n"]
    b = db.one("SELECT COUNT(*) n FROM users WHERE banned=1")["n"]
    await show(c, f"👤 <b>Пользователи</b>\nВсего: {n}, в бане: {b}", kb([[btn("🔎 Найти (ID или @username)", "adm:us")], back()]))


@router.callback_query(F.data == "adm:us")
async def cb_us(c: CallbackQuery, state: FSMContext):
    await state.set_state(A.user_search)
    await show(c, "Отправь Telegram ID или @username:", kb([back("adm:users")]))


def user_card(uid):
    u = db.one("SELECT * FROM users WHERE user_id=?", (uid,))
    if not u:
        return None, None
    n = db.one("SELECT COUNT(*) n FROM orders WHERE user_id=?", (uid,))["n"]
    paid = db.one("SELECT COUNT(*) n FROM orders WHERE user_id=? AND status='paid'", (uid,))["n"]
    text = (f"👤 <b>{('@' + u['username']) if u['username'] else uid}</b> (<code>{uid}</code>)\n"
            f"Заказов: {n}, выполнено: {paid}\nСтатус: {'🚫 в бане' if u['banned'] else 'активен'}")
    return text, kb([[btn("✅ Разбанить" if u["banned"] else "🚫 Забанить", f"adm:ub:{uid}")], back("adm:users")])


@router.message(A.user_search, F.text, ~F.text.startswith("/"))
async def got_us(m: Message, state: FSMContext):
    t = m.text.strip()
    if t.isdigit():
        uid = int(t)
    else:
        r = db.one("SELECT user_id FROM users WHERE lower(username)=lower(?)", (t.lstrip("@"),))
        uid = r["user_id"] if r else None
    text, markup = user_card(uid) if uid else (None, None)
    if not text:
        return await m.answer("Пользователь не найден (он должен был нажать /start в боте).")
    await state.clear()
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.regexp(r"^adm:ub:\d+$"))
async def cb_ub(c: CallbackQuery):
    uid = int(c.data.split(":")[2])
    if db.is_admin(uid):
        return await c.answer("Админа банить нельзя", show_alert=True)
    db.ex("UPDATE users SET banned=1-COALESCE(banned,0) WHERE user_id=?", (uid,))
    text, markup = user_card(uid)
    await show(c, text, markup)


# ---------------- рефералки ----------------
@router.callback_query(F.data == "adm:refs")
async def cb_refs(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = []
    for r in db.q("SELECT * FROM refs ORDER BY created_at DESC LIMIT 40"):
        total = db.ref_stats(r["code"])[0]
        rows.append([btn(f"🔗 {r['name']} — {total} перех.", f"adm:ref:{r['code']}")])
    rows.append([btn("➕ Создать ссылку", "adm:ref_add")])
    rows.append(back())
    await show(c, "🔗 <b>Реферальные ссылки</b>\nСоздай ссылку и раздай её — нажми на ссылку в списке, чтобы увидеть, сколько человек по ней перешло.", kb(rows))


@router.callback_query(F.data == "adm:ref_add")
async def cb_ref_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(A.ref_new)
    await show(c, "Отправь название ссылки (для себя) — например: <code>Канал Васи</code> или <code>Реклама 12.11</code>:",
               kb([back("adm:refs")]))


@router.message(A.ref_new, F.text, ~F.text.startswith("/"))
async def got_ref(m: Message, state: FSMContext, bot: Bot):
    name = m.text.strip()[:40]
    code = secrets.token_hex(4)
    db.ex("INSERT INTO refs(code,name,created_by,created_at) VALUES(?,?,?,?)", (code, name, m.from_user.id, int(time.time())))
    await state.clear()
    me = await bot.me()
    await m.answer(f"✅ Ссылка «{esc(name)}» создана:\n<code>https://t.me/{me.username}?start=ref_{code}</code>",
                   reply_markup=kb([[btn("🔗 Открыть", f"adm:ref:{code}")], [btn("🔗 К рефералкам", "adm:refs")]]))


async def ref_view(c, code, bot):
    r = db.one("SELECT * FROM refs WHERE code=?", (code,))
    if not r:
        return await c.answer("Ссылка не найдена", show_alert=True)
    me = await bot.me()
    total, new, paid = db.ref_stats(code)
    last = db.q("SELECT v.user_id, v.at, v.is_new, u.username FROM ref_visits v LEFT JOIN users u ON u.user_id=v.user_id "
                "WHERE v.code=? ORDER BY v.at DESC LIMIT 10", (code,))
    lines = [f"{time.strftime('%d.%m %H:%M', time.localtime(v['at']))} · "
             f"{('@' + esc(v['username'])) if v['username'] else v['user_id']}{' 🆕' if v['is_new'] else ''}" for v in last]
    text = (f"🔗 <b>{esc(r['name'])}</b>\n<code>https://t.me/{me.username}?start=ref_{code}</code>\n\n"
            f"👥 Перешло по ссылке: <b>{total}</b>\n🆕 Из них новых в боте: {new}\n✅ Выполненных заказов от них: {paid}\n"
            f"📅 Создана: {time.strftime('%d.%m.%Y', time.localtime(r['created_at']))}"
            + ("\n\n<b>Последние переходы:</b>\n" + "\n".join(lines) if lines else "\n\nПока никто не переходил."))
    await show(c, text, kb([[btn("🔄 Обновить", f"adm:ref:{code}"), btn("🗑 Удалить", f"adm:ref_d:{code}")], back("adm:refs")]))


@router.callback_query(F.data.regexp(r"^adm:ref:\w+$"))
async def cb_ref(c: CallbackQuery, bot: Bot):
    await ref_view(c, c.data.split(":")[2], bot)


@router.callback_query(F.data.regexp(r"^adm:ref_d:\w+$"))
async def cb_ref_d(c: CallbackQuery):
    code = c.data.split(":")[2]
    await show(c, "🗑 Удалить реферальную ссылку и статистику по ней? Ссылка перестанет считать переходы.",
               kb([[btn("Да, удалить", f"adm:ref_ok:{code}")], back(f"adm:ref:{code}")]))


@router.callback_query(F.data.regexp(r"^adm:ref_ok:\w+$"))
async def cb_ref_ok(c: CallbackQuery, state: FSMContext):
    code = c.data.split(":")[2]
    db.ex("DELETE FROM ref_visits WHERE code=?", (code,))
    db.ex("DELETE FROM refs WHERE code=?", (code,))
    await c.answer("Удалено")
    await cb_refs(c, state)


# ---------------- режим работ / бэкап ----------------
@router.callback_query(F.data == "adm:maint")
async def cb_maint(c: CallbackQuery):
    on = db.get_setting("maintenance") == "1"
    db.set_setting("maintenance", "0" if on else "1")
    await c.answer("Бот открыт для всех" if on else "Режим работ включён: обычные пользователи видят заглушку", show_alert=True)
    await show(c, "🛠 <b>Админ-панель</b>", panel_kb())


@router.callback_query(F.data == "adm:backup")
async def cb_backup(c: CallbackQuery):
    import sqlite3
    path = "backup.db"
    dst = sqlite3.connect(path)
    db._conn.backup(dst)
    dst.close()
    await c.message.answer_document(FSInputFile(path, filename=f"bot_backup_{time.strftime('%Y%m%d_%H%M')}.db"))
    await c.answer()


# ---------------- канал отзывов ----------------
@router.callback_query(F.data == "adm:rvchan")
async def cb_rvchan(c: CallbackQuery, state: FSMContext):
    await state.clear()
    chat = db.get_setting("reviews_chat")
    text = ("⭐ <b>Канал для отзывов</b>\n\nПосле подтверждения заказа бот просит покупателя поставить оценку и написать отзыв, "
            "и публикует его в этот канал.\n\nТекущий канал: " + (f"<code>{esc(chat)}</code>" if chat else "не задан (отзывы отключены)") +
            "\n\nДобавь бота в канал админом с правом публикации.")
    rows = [[btn("✏️ Указать канал", "adm:set:reviews_chat")]]
    if chat:
        rows.append([btn("📨 Тест публикации", "adm:rvtest"), btn("🚫 Отключить", "adm:rvoff")])
    rows.append(back())
    await show(c, text, kb(rows))


@router.callback_query(F.data == "adm:rvtest")
async def cb_rvtest(c: CallbackQuery, bot: Bot):
    chat = links.norm_chat(db.get_setting("reviews_chat") or "") or ""
    try:
        await bot.send_message(int(chat) if chat.lstrip("-").isdigit() else chat, "✅ Тест: бот может публиковать отзывы в этот канал.")
        await c.answer("Отправлено — проверь канал", show_alert=True)
    except Exception as e:
        await c.answer(f"Ошибка: {e}"[:190], show_alert=True)


@router.callback_query(F.data == "adm:rvoff")
async def cb_rvoff(c: CallbackQuery, state: FSMContext):
    db.set_setting("reviews_chat", "")
    await c.answer("Отзывы отключены")
    await cb_rvchan(c, state)


# ---------------- редактор всех текстов ----------------
TX_PAGE = 10


@router.callback_query(F.data == "adm:tx")
async def cb_tx(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = []
    for i, g in enumerate(texts.GROUPS):
        keys = [k for k, v in texts.TEXTS.items() if v[0] == g]
        custom = sum(texts.is_custom(k) for k in keys)
        rows.append([btn(f"{g} ({len(keys)}" + (f", изменено {custom}" if custom else "") + ")", f"adm:txg:{i}:0")])
    rows.append([btn("♻️ Сбросить все тексты", "adm:txrall")])
    rows.append(back())
    await show(c, "✏️ <b>Тексты бота</b>\nВыбери раздел. Здесь можно изменить любую кнопку и любое сообщение, "
                  "которое видят пользователи. ✏️ — текст уже изменён.", kb(rows))


@router.callback_query(F.data.regexp(r"^adm:txg:\d+:\d+$"))
async def cb_txg(c: CallbackQuery, state: FSMContext):
    await state.clear()
    _, _, gi, page = c.data.split(":")
    gi, page = int(gi), int(page)
    g = texts.GROUPS[gi]
    keys = [k for k, v in texts.TEXTS.items() if v[0] == g]
    pages = (len(keys) + TX_PAGE - 1) // TX_PAGE
    chunk = keys[page * TX_PAGE:(page + 1) * TX_PAGE]
    rows = [[btn(("✏️ " if texts.is_custom(k) else "") + texts.TEXTS[k][1], f"adm:txe:{k}")] for k in chunk]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:txg:{gi}:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("▶️", f"adm:txg:{gi}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(back("adm:tx"))
    await show(c, f"✏️ <b>{esc(g)}</b>" + (f" — стр. {page + 1}/{pages}" if pages > 1 else ""), kb(rows))


def tx_group_index(key):
    g = texts.TEXTS[key][0]
    keys = [k for k, v in texts.TEXTS.items() if v[0] == g]
    return texts.GROUPS.index(g), keys.index(key) // TX_PAGE


async def tx_view(c, key):
    gi, pg = tx_group_index(key)
    ph = texts.placeholders(key)
    text = (f"✏️ <b>{esc(texts.TEXTS[key][1])}</b>\n\nСейчас:\n<pre>{esc(texts.raw(key))}</pre>"
            + ("\nПлейсхолдеры (подставит бот): " + ", ".join(f"<code>{{{p}}}</code>" for p in ph) if ph else ""))
    rows = [[btn("✏️ Изменить", f"adm:txs:{key}")]]
    if texts.is_custom(key):
        rows.append([btn("♻️ Вернуть по умолчанию", f"adm:txr:{key}")])
    rows.append(back(f"adm:txg:{gi}:{pg}"))
    await show(c, text, kb(rows))


@router.callback_query(F.data.regexp(r"^adm:txe:\w+$"))
async def cb_txe(c: CallbackQuery, state: FSMContext):
    await state.clear()
    key = c.data.split(":")[2]
    if key not in texts.TEXTS:
        return await c.answer("Текст не найден", show_alert=True)
    await tx_view(c, key)


@router.callback_query(F.data.regexp(r"^adm:txs:\w+$"))
async def cb_txs(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":")[2]
    await state.set_state(A.text_edit)
    await state.update_data(key=key)
    ph = texts.placeholders(key)
    await show(c, f"Отправь новый текст для «{esc(texts.TEXTS[key][1])}».\nМожно использовать форматирование Telegram (жирный, курсив, код)."
                  + (("\nСохрани плейсхолдеры: " + ", ".join(f"<code>{{{p}}}</code>" for p in ph)) if ph else ""),
               kb([back(f"adm:txe:{key}")]))


@router.message(A.text_edit, F.text, ~F.text.startswith("/"))
async def got_text_edit(m: Message, state: FSMContext):
    d = await state.get_data()
    key = d["key"]
    new = (m.text or "") if key.startswith("btn_") else (m.html_text or "")
    if not new.strip():
        return await m.answer("Текст не должен быть пустым.")
    db.set_setting(key, new)
    await state.clear()
    missing = [p for p in texts.placeholders(key) if "{" + p + "}" not in new]
    warn = ("\n⚠️ Нет плейсхолдеров: " + ", ".join(f"<code>{{{p}}}</code>" for p in missing) + " — этих данных в тексте не будет.") if missing else ""
    await m.answer("✅ Текст сохранён." + warn, reply_markup=kb([[btn("✏️ К тексту", f"adm:txe:{key}")]]))


@router.callback_query(F.data.regexp(r"^adm:txr:\w+$"))
async def cb_txr(c: CallbackQuery):
    key = c.data.split(":")[2]
    if key in texts.TEXTS:
        texts.reset(key)
        await c.answer("Возвращено по умолчанию")
        await tx_view(c, key)


@router.callback_query(F.data == "adm:txrall")
async def cb_txrall(c: CallbackQuery):
    await show(c, "♻️ Сбросить <b>все</b> тексты и названия кнопок к значениям по умолчанию?",
               kb([[btn("Да, сбросить", "adm:txrall_ok")], back("adm:tx")]))


@router.callback_query(F.data == "adm:txrall_ok")
async def cb_txrall_ok(c: CallbackQuery, state: FSMContext):
    for k in texts.TEXTS:
        texts.reset(k)
    await c.answer("Все тексты сброшены")
    await cb_tx(c, state)


# ---------------- запасной обработчик (должен быть последним) ----------------
@router.message(StateFilter(A), ~F.text)
async def not_text(m: Message):
    await m.answer("Нужно отправить обычное текстовое сообщение (или картинку, если бот просит картинку). Выйти — /admin.")
