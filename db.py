import os
import sqlite3
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
    "manager": "@your_manager",
    "reviews_url": "https://t.me/",
    "channel_url": "https://t.me/",
    "reviews_chat": "",
    "maintenance": "0",
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
        """
    )
    for tbl, col in [("tags", "slots INTEGER"), ("prices", "url TEXT"), ("orders", "tag_id INTEGER"),
                     ("categories", "banner TEXT"), ("tags", "banner TEXT"), ("tags", "hidden INTEGER DEFAULT 0"),
                     ("orders", "proof TEXT"), ("orders", "reviewed INTEGER DEFAULT 0"), ("users", "banned INTEGER DEFAULT 0"),
                     ("tags", "closed INTEGER DEFAULT 0"), ("prices", "closed INTEGER DEFAULT 0")]:
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
    return r["code"], percent


def consume_promo(user_id: int, code: str):
    ex("UPDATE promocodes SET used_count=used_count+1 WHERE code=?", (code,))
    ex("INSERT INTO promo_uses VALUES(?,?)", (code, user_id))
    ex("DELETE FROM user_promo WHERE user_id=?", (user_id,))


def apply_discount(price, percent):
    if price is None:
        return None
    if not percent:
        return price
    return max(1, round(price * (100 - percent) / 100))
