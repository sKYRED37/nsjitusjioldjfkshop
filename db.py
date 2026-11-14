import os
import sqlite3
import random
import time

# Путь к базе: на хостинге можно задать переменной DB_PATH (например /data/bot.db на постоянном диске)
USER_PAGE = 8  # тегов на странице списка у покупателя
DB_PATH = os.environ.get("DB_PATH") or "bot.db"
_dir = os.path.dirname(os.path.abspath(DB_PATH))
os.makedirs(_dir, exist_ok=True)
_conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
_conn.row_factory = sqlite3.Row
try:
    _conn.execute("PRAGMA journal_mode=WAL")
except sqlite3.DatabaseError:
    pass

DEFAULT_SETTINGS = {
    "coins_percent": "5",  # % монет от суммы оплаты картой/СБП
    "coins_mult": "3",     # цена тега в койнах = цена в ₽ × это число
    "bonus_min": "5",      # ежедневный бонус: от скольких монет
    "bonus_max": "100",    # ежедневный бонус: до скольких монет
    "bonus_curve": "3",    # редкость больших наград: 1 — все суммы равновероятны, чем больше — тем реже крупные
    "manager": "@your_manager",
    "reviews_url": "https://t.me/",
    "channel_url": "https://t.me/",
    "reviews_chat": "",
    "maintenance": "0",
    "stars_contact": "",   # юзернейм, куда писать при оплате звёздами (пусто = менеджер)
    "boost_contact": "",   # юзернейм, куда писать при оплате бустами (пусто = менеджер)
    "payment_details": "Карта: 0000 0000 0000 0000\nПолучатель: Имя Ф.\nСБП: +7 000 000-00-00\n\nВ комментарии к переводу ничего писать не нужно.",
}


def q(sql, args=()):
    return _conn.execute(sql, args).fetchall()


def one(sql, args=()):
    return _conn.execute(sql, args).fetchone()


def ex(sql, args=()):
    cur = _conn.execute(sql, args)
    _conn.commit()
    return cur.lastrowid




def claim_case_order(order_id: int):
    """Подтверждает оплату и помещает неоткрытый кейс в инвентарь покупателя."""
    _conn.execute("BEGIN IMMEDIATE")
    try:
        order = _conn.execute(
            "SELECT * FROM case_orders WHERE id=? AND status='pending'", (int(order_id),)
        ).fetchone()
        if not order:
            _conn.commit()
            return None
        pool = _conn.execute(
            """SELECT 1 FROM case_tags ct JOIN tags t ON t.id=ct.tag_id
               WHERE ct.case_id=? AND COALESCE(t.hidden,0)=0
                 AND COALESCE(t.closed,0)=0
                 AND (t.slots IS NULL OR t.slots>0) LIMIT 1""",
            (order["case_id"],)
        ).fetchone()
        if not pool:
            _conn.commit()
            return {"error": "empty_pool"}
        cur = _conn.execute(
            "UPDATE case_orders SET status='paid' WHERE id=? AND status='pending'",
            (int(order_id),)
        )
        if cur.rowcount != 1:
            _conn.rollback()
            return None
        cur = _conn.execute(
            "INSERT INTO case_inventory(user_id,case_id,price,created_at) VALUES(?,?,?,?)",
            (order["user_id"], order["case_id"], order["amount"], int(time.time()))
        )
        inventory_id = cur.lastrowid
        _conn.commit()
        return {"order": order, "inventory_id": inventory_id}
    except Exception:
        _conn.rollback()
        raise


def open_case_inventory(inventory_id: int, user_id: int):
    """Атомарно открывает купленный кейс и фиксирует единственный результат."""
    _conn.execute("BEGIN IMMEDIATE")
    try:
        item = _conn.execute(
            "SELECT * FROM case_inventory WHERE id=? AND user_id=? AND opened_at IS NULL",
            (int(inventory_id), int(user_id))
        ).fetchone()
        if not item:
            _conn.commit()
            return None
        pool = _conn.execute(
            """SELECT t.id, t.name FROM case_tags ct JOIN tags t ON t.id=ct.tag_id
               WHERE ct.case_id=? AND COALESCE(t.hidden,0)=0 AND COALESCE(t.closed,0)=0
                 AND (t.slots IS NULL OR t.slots>0) ORDER BY t.id""",
            (item["case_id"],)
        ).fetchall()
        if not pool:
            _conn.commit()
            return {"error": "empty_pool"}
        chosen = random.choice(pool)
        now = int(time.time())
        cur = _conn.execute(
            "INSERT INTO case_draws(user_id,case_id,tag_id,price,created_at) VALUES(?,?,?,?,?)",
            (int(user_id), item["case_id"], int(chosen["id"]), item["price"], now)
        )
        draw_id = cur.lastrowid
        cur = _conn.execute(
            "UPDATE case_inventory SET opened_at=?,draw_id=? WHERE id=? AND user_id=? AND opened_at IS NULL",
            (now, draw_id, int(inventory_id), int(user_id))
        )
        if cur.rowcount != 1:
            _conn.rollback()
            return None
        case = _conn.execute("SELECT name FROM cases WHERE id=?", (item["case_id"],)).fetchone()
        _conn.commit()
        return {"inventory": item, "tag_id": int(chosen["id"]), "tag_name": chosen["name"],
                "case_name": case["name"] if case else "Кейс", "draw_id": draw_id,
                "pool_names": [x["name"] for x in pool]}
    except Exception:
        _conn.rollback()
        raise


def reject_case_order(order_id: int) -> bool:
    """Атомарно отклоняет только ещё ожидающий заказ."""
    _conn.execute("BEGIN IMMEDIATE")
    try:
        cur = _conn.execute(
            "UPDATE case_orders SET status='rejected' WHERE id=? AND status='pending'",
            (int(order_id),)
        )
        _conn.commit()
        return cur.rowcount == 1
    except Exception:
        _conn.rollback()
        raise


def init(owner_id: int):
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS admins(
            user_id INTEGER PRIMARY KEY, added_by INTEGER, added_at INTEGER);
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT, joined_at INTEGER);
        CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, position INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS tags(
            id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER, name TEXT, description TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS prices(
            id INTEGER PRIMARY KEY AUTOINCREMENT, tag_id INTEGER, term TEXT, price_rub INTEGER, price_gold INTEGER);
        CREATE TABLE IF NOT EXISTS promocodes(
            code TEXT PRIMARY KEY, percent INTEGER, max_uses INTEGER DEFAULT 0,
            used_count INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS promo_uses(code TEXT, user_id INTEGER);
        CREATE TABLE IF NOT EXISTS user_promo(user_id INTEGER PRIMARY KEY, code TEXT);
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, game_id TEXT,
            tag TEXT, term TEXT, method TEXT, amount INTEGER, promo TEXT,
            status TEXT DEFAULT 'pending', created_at INTEGER);
        CREATE TABLE IF NOT EXISTS order_numbers(id INTEGER PRIMARY KEY AUTOINCREMENT);
        CREATE TABLE IF NOT EXISTS page_banners(category_id INTEGER, page INTEGER, file_id TEXT, PRIMARY KEY(category_id, page));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS refs(
            code TEXT PRIMARY KEY, name TEXT, created_by INTEGER, created_at INTEGER);
        CREATE TABLE IF NOT EXISTS coin_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, delta INTEGER, kind TEXT,
            tag TEXT, term TEXT, order_id INTEGER, created_at INTEGER);
        CREATE INDEX IF NOT EXISTS idx_coin_log_user ON coin_log(user_id);
        CREATE TABLE IF NOT EXISTS bonus_state(user_id INTEGER PRIMARY KEY, last_at INTEGER);
        CREATE TABLE IF NOT EXISTS favorites(
            user_id INTEGER, tag_id INTEGER, added_at INTEGER,
            last_disc INTEGER DEFAULT 0, last_price INTEGER, end_warned INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, tag_id));
        CREATE INDEX IF NOT EXISTS idx_fav_tag ON favorites(tag_id);
        CREATE TABLE IF NOT EXISTS ref_visits(
            code TEXT, user_id INTEGER, is_new INTEGER DEFAULT 0, at INTEGER,
            PRIMARY KEY(code, user_id));
        CREATE TABLE IF NOT EXISTS cases(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, rarity TEXT NOT NULL,
            price INTEGER NOT NULL DEFAULT 0, active INTEGER DEFAULT 1, price_rub INTEGER DEFAULT 0, price_gold INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS case_tags(
            case_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY(case_id, tag_id));
        CREATE TABLE IF NOT EXISTS case_draws(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, case_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL, price INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS case_inventory(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, case_id INTEGER NOT NULL,
            price INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
            opened_at INTEGER, draw_id INTEGER);
        CREATE INDEX IF NOT EXISTS idx_case_inventory_user ON case_inventory(user_id, opened_at);
        CREATE TABLE IF NOT EXISTS case_orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT,
            case_id INTEGER NOT NULL, method TEXT NOT NULL, amount INTEGER NOT NULL,
            proof TEXT NOT NULL, status TEXT DEFAULT 'pending', tag_id INTEGER, created_at INTEGER NOT NULL);
        """
    )
    for tbl, col in [("tags", "slots INTEGER"), ("prices", "url TEXT"), ("orders", "tag_id INTEGER"),
                     ("categories", "banner TEXT"), ("tags", "banner TEXT"), ("tags", "hidden INTEGER DEFAULT 0"),
                     ("orders", "proof TEXT"), ("orders", "reviewed INTEGER DEFAULT 0"), ("users", "banned INTEGER DEFAULT 0"),
                     ("tags", "closed INTEGER DEFAULT 0"), ("prices", "closed INTEGER DEFAULT 0"),
                     ("prices", "price_stars INTEGER"), ("prices", "price_boosts INTEGER"),
                     ("promocodes", "tag_id INTEGER"),
                     ("prices", "coins INTEGER"), ("orders", "price_id INTEGER"),
                     ("tags", "coin_hidden INTEGER DEFAULT 0"),
                     ("tags", "hit INTEGER DEFAULT 0"),        # хит продаж
                     ("tags", "disc_pct INTEGER"),             # скидка на тег, %
                     ("tags", "disc_until INTEGER"), ("cases", "price_rub INTEGER DEFAULT 0"), ("cases", "price_gold INTEGER DEFAULT 0"),
                     ("case_draws", "game_id TEXT"), ("case_draws", "issue_status TEXT DEFAULT 'not_requested'"),
                     ("case_draws", "requested_at INTEGER"), ("case_draws", "decided_at INTEGER")]:          # до какого времени (unix), NULL = бессрочно  # coins: своё число монет за покупку (NULL = % от цены)  # tag_id: промокод только на этот тег (NULL = на все)
        try:
            _conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    for k, v in DEFAULT_SETTINGS.items():
        _conn.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
    owners = owner_id if isinstance(owner_id, (list, tuple, set)) else [owner_id]
    for oid in owners:
        if oid:
            _conn.execute(
                "INSERT OR IGNORE INTO admins VALUES(?,?,?)", (int(oid), int(oid), int(time.time()))
            )
    _conn.commit()
    if not one("SELECT 1 FROM categories"):
        _seed()


def _seed():
    c1 = ex("INSERT INTO categories(name) VALUES('Киберспортивные теги')")
    ex("INSERT INTO categories(name) VALUES('Фан-теги')")
    t = ex("INSERT INTO tags(category_id,name,description,slots) VALUES(?,?,?,?)", (c1, "[ANBL]", "Anable", 45))
    for term, r, g in [("3 дня", 100, 200), ("Неделя", 200, 400), ("Месяц", 550, 1100), ("Год", 2650, 5300)]:
        ex("INSERT INTO prices(tag_id,term,price_rub,price_gold) VALUES(?,?,?,?)", (t, term, r, g))


def reserve_order_number():
    """Выдаёт уникальный номер заказа заранее (показывается на экране оплаты)."""
    return ex("INSERT INTO order_numbers(id) SELECT MAX(COALESCE((SELECT MAX(id) FROM orders),0), "
              "COALESCE((SELECT MAX(id) FROM order_numbers),0)) + 1")


def page_banner(cat_id, page):
    r = one("SELECT file_id FROM page_banners WHERE category_id=? AND page=?", (cat_id, page))
    return r["file_id"] if r and r["file_id"] else None


def visible_tag_count(cat_id):
    return one("SELECT COUNT(*) n FROM tags WHERE category_id=? AND hidden=0", (cat_id,))["n"]


# ---------- settings / admins ----------
def get_setting(key):
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):
    ex("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, value))


def is_admin(user_id: int) -> bool:
    return one("SELECT 1 FROM admins WHERE user_id=?", (user_id,)) is not None


def admin_ids():
    return [r["user_id"] for r in q("SELECT user_id FROM admins")]


# ---------- promo ----------
def check_promo(code: str, user_id: int):
    """Возвращает (percent, error)."""
    code = code.strip().upper()
    p = one("SELECT * FROM promocodes WHERE code=?", (code,))
    if not p or not p["active"]:
        return None, "none"
    if p["max_uses"] and p["used_count"] >= p["max_uses"]:
        return None, "limit"
    if one("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, user_id)):
        return None, "used"
    return p["percent"], None


def activate_promo(user_id: int, code: str):
    ex("INSERT OR REPLACE INTO user_promo VALUES(?,?)", (user_id, code.strip().upper()))


def active_promo(user_id: int):
    """Активный промокод пользователя (code, percent) или None. Перепроверяет валидность."""
    r = one("SELECT code FROM user_promo WHERE user_id=?", (user_id,))
    if not r:
        return None
    percent, err = check_promo(r["code"], user_id)
    if err:
        ex("DELETE FROM user_promo WHERE user_id=?", (user_id,))
        return None
    p = one("SELECT tag_id FROM promocodes WHERE code=?", (r["code"],))
    return r["code"], percent, (p["tag_id"] if p else None)


def pct_for(pr, tag_id):
    """Процент скидки промокода pr (из active_promo) для тега tag_id: 0, если промокод на другой тег."""
    if not pr:
        return 0
    if pr[2] is not None and pr[2] != tag_id:
        return 0
    return pr[1]


def promo_tag(code: str):
    """Тег, к которому привязан промокод (строка tags), либо None, если промокод на все теги."""
    return one("SELECT t.* FROM promocodes p JOIN tags t ON t.id=p.tag_id WHERE p.code=?", (code.strip().upper(),))


def consume_promo(user_id: int, code: str):
    ex("UPDATE promocodes SET used_count=used_count+1 WHERE code=?", (code,))
    ex("INSERT INTO promo_uses VALUES(?,?)", (code, user_id))
    ex("DELETE FROM user_promo WHERE user_id=?", (user_id,))


# ---------- хит продаж и скидки на теги ----------
def disc_of(tg):
    """Действующая скидка тега в % (0 — нет или уже истекла). tg — строка из таблицы tags."""
    pct = tg["disc_pct"] if tg is not None else 0
    if not pct:
        return 0
    until = tg["disc_until"]
    if until is not None and until <= int(time.time()):
        return 0
    return int(pct)


def disc_left(tg):
    """Секунд до конца скидки; None — скидка бессрочная (или её нет)."""
    if not disc_of(tg) or tg["disc_until"] is None:
        return None
    return max(0, int(tg["disc_until"]) - int(time.time()))


def tag_pct(tag_id):
    return disc_of(one("SELECT disc_pct, disc_until FROM tags WHERE id=?", (tag_id,)))


def combine_pct(promo_pct, tag_disc):
    """Скидка тега и промокод складываются последовательно: (1-a)*(1-b)."""
    a, b = int(promo_pct or 0), int(tag_disc or 0)
    if not a or not b:
        return a or b
    return min(99, round(100 - (100 - a) * (100 - b) / 100))


def total_pct(pr, tag_id):
    """Итоговый процент для тега: скидка тега + промокод (pr — из active_promo)."""
    return combine_pct(pct_for(pr, tag_id), tag_pct(tag_id))


def set_discount(tag_ids, pct, seconds):
    """Ставит скидку pct% на теги. seconds=0 — бессрочно."""
    until = int(time.time()) + int(seconds) if seconds else None
    for tid in tag_ids:
        _conn.execute("UPDATE tags SET disc_pct=?, disc_until=? WHERE id=?", (int(pct), until, int(tid)))
    _conn.commit()


def clear_discount(tag_ids):
    for tid in tag_ids:
        _conn.execute("UPDATE tags SET disc_pct=NULL, disc_until=NULL WHERE id=?", (int(tid),))
    _conn.commit()


def toggle_hit(tag_id):
    ex("UPDATE tags SET hit=1-COALESCE(hit,0) WHERE id=?", (int(tag_id),))


def apply_discount(price, percent):
    if price is None:
        return None
    if not percent:
        return price
    return max(1, round(price * (100 - percent) / 100))


# ---------- способы оплаты ----------
# method в таблице orders: card (₽), gold (г), stars (звёзды), boost (бусты)
METHOD_UNIT = {"card": "₽", "gold": "г", "stars": "⭐", "boost": "🚀", "coins": "🪙"}
METHOD_NAME = {"card": "карта/СБП", "gold": "голда", "stars": "звёзды", "boost": "бусты", "coins": "монеты"}


def money(amount, method):
    """'1 200 ₽' / '400 г' / '50 ⭐' / '3 🚀'."""
    return f"{amount:,}".replace(",", " ") + " " + METHOD_UNIT.get(method, "")


# ---------- монеты ----------
def coin_percent():
    try:
        return max(0, min(100, int(get_setting("coins_percent") or 0)))
    except ValueError:
        return 0


def coin_mult():
    try:
        return max(1, int(get_setting("coins_mult") or 3))
    except ValueError:
        return 3


def coin_balance(uid):
    return one("SELECT COALESCE(SUM(delta),0) s FROM coin_log WHERE user_id=?", (uid,))["s"]


def coin_add(uid, delta, kind, tag="", term="", order_id=None):
    """kind: earn — начисление за заказ, spend — оплата монетами, refund — возврат, admin — ручная правка."""
    return ex("INSERT INTO coin_log(user_id,delta,kind,tag,term,order_id,created_at) VALUES(?,?,?,?,?,?,?)",
              (uid, int(delta), kind, tag or "", term or "", order_id, int(time.time())))


def coins_for(rub):
    """Цена в койнах = цена в рублях × множитель (по умолчанию ×3)."""
    return int(rub) * coin_mult()


def coins_earn_for(amount_rub):
    return int(amount_rub) * coin_percent() // 100


def coins_reward(price, paid_rub=None):
    """Сколько монет дать за покупку: своё число у цены или % от суммы (оплаченной картой — фактической, иначе — от цены)."""
    if price is not None and price["coins"] is not None:
        return max(0, int(price["coins"]))
    base = paid_rub if paid_rub is not None else (price["price_rub"] if price is not None else 0)
    return coins_earn_for(base or 0)


BONUS_HOURS = 24


def bonus_range():
    try:
        lo, hi = int(get_setting("bonus_min") or 5), int(get_setting("bonus_max") or 100)
    except ValueError:
        lo, hi = 5, 100
    lo = max(1, lo)
    return lo, max(lo, hi)


def bonus_curve():
    try:
        return max(1, min(10, int(get_setting("bonus_curve") or 3)))
    except ValueError:
        return 3


def bonus_roll():
    """Случайная награда: маленькие суммы выпадают часто, большие — редко (чем выше bonus_curve, тем реже крупные)."""
    lo, hi = bonus_range()
    n = lo + int((hi - lo + 1) * random.random() ** bonus_curve())
    return min(n, hi)


def bonus_left(uid):
    """Сколько секунд осталось до следующего бонуса (0 — можно забирать)."""
    r = one("SELECT last_at FROM bonus_state WHERE user_id=?", (uid,))
    if not r or not r["last_at"]:
        return 0
    return max(0, int(r["last_at"]) + BONUS_HOURS * 3600 - int(time.time()))


def bonus_claim(uid):
    """Выдаёт случайный бонус, если прошло 24 ч. Возвращает число монет или None. Без await внутри — двойной тап не страшен."""
    if bonus_left(uid) > 0:
        return None
    n = bonus_roll()
    ex("INSERT OR REPLACE INTO bonus_state(user_id,last_at) VALUES(?,?)", (uid, int(time.time())))
    coin_add(uid, n, "bonus")
    return n


# ---------- рефералки ----------
def ref_stats(code):
    """(переходов, из них новых, оплаченных заказов) по реферальной ссылке."""
    total = one("SELECT COUNT(*) n FROM ref_visits WHERE code=?", (code,))["n"]
    new = one("SELECT COUNT(*) n FROM ref_visits WHERE code=? AND is_new=1", (code,))["n"]
    paid = one("SELECT COUNT(*) n FROM orders o JOIN ref_visits v ON v.user_id=o.user_id "
               "WHERE v.code=? AND o.status='paid'", (code,))["n"]
    return total, new, paid


# ---------- избранное ----------
FAV_WARN_HOURS = 24  # за сколько часов до конца скидки предупреждать «скоро подорожает»


def tag_base_price(tag_id):
    """Минимальная цена тега в ₽ без скидок (по незакрытым срокам) — для отслеживания подорожания."""
    r = one("SELECT MIN(price_rub) m FROM prices WHERE tag_id=? AND COALESCE(closed,0)=0", (tag_id,))
    return r["m"] if r and r["m"] is not None else None


def is_fav(uid, tag_id):
    return one("SELECT 1 FROM favorites WHERE user_id=? AND tag_id=?", (uid, tag_id)) is not None


def fav_toggle(uid, tag_id):
    """Добавляет тег в избранное или убирает. Возвращает True, если теперь в избранном."""
    if is_fav(uid, tag_id):
        ex("DELETE FROM favorites WHERE user_id=? AND tag_id=?", (uid, tag_id))
        return False
    tg = one("SELECT disc_pct, disc_until FROM tags WHERE id=?", (tag_id,))
    ex("INSERT OR IGNORE INTO favorites(user_id,tag_id,added_at,last_disc,last_price,end_warned) VALUES(?,?,?,?,?,0)",
       (uid, tag_id, int(time.time()), disc_of(tg), tag_base_price(tag_id)))
    return True


def fav_tags(uid):
    return q("SELECT t.* FROM favorites f JOIN tags t ON t.id=f.tag_id WHERE f.user_id=? AND t.hidden=0 "
             "ORDER BY f.added_at DESC", (uid,))


def fav_events():
    """Проверяет все избранные теги и возвращает список уведомлений [(user_id, tag_row, kind, extra)].
    kind: discount — появилась/выросла скидка; ending — скидка скоро закончится (тег подорожает);
    raised — цена выросла; ended — скидка закончилась. Снимок состояния обновляется сразу."""
    out = []
    rows = q("SELECT f.user_id, f.last_disc, f.last_price, f.end_warned, t.* FROM favorites f "
             "JOIN tags t ON t.id=f.tag_id LEFT JOIN users u ON u.user_id=f.user_id "
             "WHERE t.hidden=0 AND COALESCE(u.banned,0)=0")
    for r in rows:
        cur_disc = disc_of(r)
        left = disc_left(r)
        base = tag_base_price(r["id"])
        last_disc = r["last_disc"] or 0
        last_price = r["last_price"]
        warned = r["end_warned"] or 0
        kind = None
        if cur_disc > last_disc:
            kind, warned = "discount", 0
        elif cur_disc and left is not None and left <= FAV_WARN_HOURS * 3600 and not warned:
            kind, warned = "ending", 1
        elif base is not None and last_price is not None and base > last_price:
            kind = "raised"
        elif last_disc and not cur_disc:
            kind, warned = "ended", 0
        if cur_disc and left is not None and left > FAV_WARN_HOURS * 3600:
            warned = 0
        if (cur_disc, base, warned) != (last_disc, last_price, r["end_warned"] or 0):
            _conn.execute("UPDATE favorites SET last_disc=?, last_price=?, end_warned=? WHERE user_id=? AND tag_id=?",
                          (cur_disc, base, warned, r["user_id"], r["id"]))
        if kind:
            out.append((r["user_id"], r, kind, {"old": last_price, "new": base, "left": left, "pct": cur_disc}))
    _conn.commit()
    return out
