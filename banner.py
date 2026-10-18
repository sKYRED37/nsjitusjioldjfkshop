"""Баннер «Выбери срок»: берётся готовый шаблон assets/terms_template.png,
динамические части (название тега, сроки, полоски, цены) стираются и рисуются заново."""
import io
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "assets", "terms_template.png")
FONT = os.path.join(HERE, "fonts", "Montserrat.ttf")
S = 3  # суперсэмплинг для гладких краёв

BG = (3, 3, 3)
TRACK = (32, 31, 32)
RED = (246, 3, 22)
WHITE = (252, 250, 251)
GOLD = (255, 206, 84)

# геометрия шаблона (1896x830)
CHIP_CLEAR = (1338, 122, 1528, 208)      # область, где был текст «TAG»
CHIP_CENTER = (1433, 165)
ROWS_CLEAR = (1098, 250, 1796, 636)      # область со строками сроков
ROWS_CENTER_Y = 437
LABEL_X = 1103
TRACK_X0, TRACK_X1 = 1325, 1565
TRACK_H = 50
PRICE_RIGHT = 1764
MAX_PITCH = 92

_cache = {}
_tpl = None


def font(size, weight):
    key = (size, weight)
    if key not in _cache:
        f = ImageFont.truetype(FONT, int(size * S))
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        _cache[key] = f
    return _cache[key]


def fmt(n):
    return f"{int(n):,}".replace(",", " ")


def fit(text, weight, size, max_w, min_size=18):
    """Уменьшает шрифт, пока текст не влезет в max_w."""
    while size > min_size and font(size, weight).getlength(text) / S > max_w:
        size -= 2
    return font(size, weight)


def chamfer(d, box, fill, cut=9):
    x0, y0, x1, y1 = [v * S for v in box]
    c = cut * S
    d.polygon([(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1), (x0 + c, y1),
               (x0, y1 - c), (x0, y0 + c)], fill=fill)


def template():
    global _tpl
    if _tpl is None:
        _tpl = Image.open(TEMPLATE).convert("RGB")
    return _tpl.copy()


def render_terms(tag, rows, highlight=None, **_ignored):
    """rows: [(срок, цена_в_рублях | None, закрыт_ли)] → байты JPEG."""
    base = template()
    W, H = base.size
    # 1) стираем динамические области
    cl = ImageDraw.Draw(base)
    cl.rectangle(CHIP_CLEAR, fill=BG)
    cl.rectangle(ROWS_CLEAR, fill=BG)

    # 2) рисуем в большом разрешении поверх и вклеиваем
    layer = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # название тега (без квадратных скобок — скобки уже есть в шаблоне)
    name = str(tag).strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    name = name.upper()
    cw = CHIP_CLEAR[2] - CHIP_CLEAR[0] - 12
    f = fit(name, 850, 70, cw, 30)
    d.text((CHIP_CENTER[0] * S, CHIP_CENTER[1] * S), name, font=f, fill=WHITE, anchor="mm")

    rows = rows[:8]
    n = max(len(rows), 1)
    pitch = min(MAX_PITCH, 380 // n)
    track_h = min(TRACK_H, pitch - 12)
    fs = min(44, int(track_h * 0.9))
    top = ROWS_CENTER_Y - (n - 1) * pitch / 2
    prices = [r[1] for r in rows if r[1] and not r[2]]
    mx = max(prices) if prices else 1
    last_open = max((i for i, r in enumerate(rows) if not r[2] and r[1]), default=None)
    for i, row in enumerate(rows):
        term, price, closed = row[:3]
        gold = row[3] if len(row) > 3 else None
        cy = top + i * pitch
        hl = (i == highlight) and not closed
        lab = fit(term, 700, fs, TRACK_X0 - LABEL_X - 14)
        d.text((LABEL_X * S, cy * S), term, font=lab,
               fill=RED if hl else ((130, 130, 130) if closed else WHITE), anchor="lm")
        chamfer(d, (TRACK_X0, cy - track_h / 2, TRACK_X1, cy + track_h / 2), TRACK)
        if price and not closed:
            fw = 55 + (price / mx) * (TRACK_X1 - TRACK_X0 - 60)
            col = WHITE if (i == last_open and n > 1 and price == mx) else RED
            chamfer(d, (TRACK_X0, cy - track_h / 2, TRACK_X0 + fw, cy + track_h / 2), col)
        maxw = PRICE_RIGHT - TRACK_X1 - 22
        if price and not closed:
            if gold:
                rs = min(40, int(pitch * 0.52))
                pf = fit(f"{fmt(price)} ₽", 700, rs, maxw)
                gf = fit(f"{fmt(gold)} г", 650, int(rs * 0.7), maxw)
                d.text((PRICE_RIGHT * S, (cy - gf.size / S * 0.55) * S), f"{fmt(price)} ₽", font=pf,
                       fill=WHITE, anchor="rm")
                d.text((PRICE_RIGHT * S, (cy + pf.size / S * 0.5) * S), f"{fmt(gold)} г", font=gf,
                       fill=GOLD, anchor="rm")
            else:
                pf = fit(f"{fmt(price)} ₽", 700, fs, maxw)
                d.text((PRICE_RIGHT * S, cy * S), f"{fmt(price)} ₽", font=pf, fill=WHITE, anchor="rm")
        else:
            pf = fit("закрыто", 700, fs, maxw)
            d.text((PRICE_RIGHT * S, cy * S), "закрыто", font=pf, fill=(130, 130, 130), anchor="rm")

    layer = layer.resize((W, H), Image.LANCZOS)
    base.paste(layer, (0, 0), layer)
    out = io.BytesIO()
    base.save(out, "JPEG", quality=90)
    return out.getvalue()


ID_TEMPLATE = os.path.join(HERE, "assets", "id_template.png")
ID_CLEAR = (1336, 168, 1516, 246)   # где был текст «TAG» в чипе
ID_CENTER = (1426, 206)
_id_tpl = None


def render_id(tag, **_ignored):
    """Баннер «Игровой ID» с названием покупаемого тега в чипе → байты JPEG."""
    global _id_tpl
    if _id_tpl is None:
        _id_tpl = Image.open(ID_TEMPLATE).convert("RGB")
    base = _id_tpl.copy()
    W, H = base.size
    ImageDraw.Draw(base).rectangle(ID_CLEAR, fill=BG)
    layer = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    name = str(tag).strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    f = fit(name.upper(), 850, 64, ID_CLEAR[2] - ID_CLEAR[0] - 8, 26)
    ImageDraw.Draw(layer).text((ID_CENTER[0] * S, ID_CENTER[1] * S), name.upper(), font=f, fill=WHITE, anchor="mm")
    layer = layer.resize((W, H), Image.LANCZOS)
    base.paste(layer, (0, 0), layer)
    out = io.BytesIO()
    base.save(out, "JPEG", quality=90)
    return out.getvalue()


ORDER_TEMPLATE = os.path.join(HERE, "assets", "order_template.png")
ORDER_BG = (1, 1, 2)
_order_tpl = None


def render_order(game_id, tag, term, rub, gold=None, **_ignored):
    """Баннер «Проверь заказ»: ID, тег, срок и итог (рубли и, если есть, голда) → байты JPEG."""
    global _order_tpl
    if _order_tpl is None:
        _order_tpl = Image.open(ORDER_TEMPLATE).convert("RGB")
    base = _order_tpl.copy()
    W, H = base.size
    cl = ImageDraw.Draw(base)
    for box in ((1470, 180, 1700, 255), (1512, 272, 1650, 342), (1470, 352, 1700, 428), (1450, 495, 1700, 612)):
        cl.rectangle(box, fill=ORDER_BG)
    layer = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    R = 1683

    gid = str(game_id)
    d.text((R * S, 217 * S), gid, font=fit(gid, 700, 46, 205, 24), fill=WHITE, anchor="rm")

    name = str(tag).strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    name = name.upper()
    d.text((1581 * S, 306 * S), name, font=fit(name, 850, 44, 124, 20), fill=WHITE, anchor="mm")

    d.text((R * S, 390 * S), str(term), font=fit(str(term), 700, 46, 200, 22), fill=WHITE, anchor="rm")

    total = f"{fmt(rub)} ₽"
    if gold:
        d.text((1688 * S, 530 * S), total, font=fit(total, 850, 60, 215, 26), fill=RED, anchor="rm")
        g = f"{fmt(gold)} г"
        d.text((1688 * S, 590 * S), g, font=fit(g, 700, 30, 215, 18), fill=GOLD, anchor="rm")
    else:
        d.text((1688 * S, 543 * S), total, font=fit(total, 850, 64, 215, 26), fill=RED, anchor="rm")

    layer = layer.resize((W, H), Image.LANCZOS)
    base.paste(layer, (0, 0), layer)
    out = io.BytesIO()
    base.save(out, "JPEG", quality=90)
    return out.getvalue()


PAY_TEMPLATE = os.path.join(HERE, "assets", "pay_template.png")
EXO = os.path.join(HERE, "fonts", "Exo2.ttf")
_pay_tpl = None
_exo_cache = {}


def exo(size, weight=500):
    key = (size, weight)
    if key not in _exo_cache:
        f = ImageFont.truetype(EXO, int(size * S))
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        _exo_cache[key] = f
    return _exo_cache[key]


def _wrap(text, f, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if f.getlength(t) / S <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_pay(amount, unit="₽", subtitle=None, **_ignored):
    """Баннер «Оплата заказа»: сумма «К оплате» (₽ или г). subtitle — своя подпись слева (иначе из шаблона)."""
    global _pay_tpl
    if _pay_tpl is None:
        _pay_tpl = Image.open(PAY_TEMPLATE).convert("RGB")
    base = _pay_tpl.copy()
    W, H = base.size
    px = base.load()
    # стираем сумму: заливаем строки плавным переходом между левым и правым краем плашки
    x0, x1, y0, y1 = 1175, 1525, 480, 602
    for y in range(y0, y1):
        l, r = px[x0 - 3, y], px[x1 + 3, y]
        for x in range(x0, x1):
            k = (x - x0) / (x1 - x0)
            px[x, y] = tuple(int(l[i] * (1 - k) + r[i] * k) for i in range(3))
    if subtitle:
        ImageDraw.Draw(base).rectangle((105, 566, 812, 668), fill=(0, 0, 1))

    layer = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    txt = f"{fmt(amount)} {unit}"
    f = fit(txt, 850, 118, 290, 40)
    d.text((1357 * S, 541 * S), txt, font=f, fill=WHITE, anchor="mm")
    if subtitle:
        sf = exo(37, 500)
        for i, line in enumerate(_wrap(subtitle, sf, 690)[:2]):
            d.text((113 * S, (592 + i * 48) * S), line, font=sf, fill=(236, 234, 248), anchor="lm")
    layer = layer.resize((W, H), Image.LANCZOS)
    glow = layer.filter(ImageFilter.GaussianBlur(5))
    base.paste(glow, (0, 0), glow)
    base.paste(layer, (0, 0), layer)
    out = io.BytesIO()
    base.save(out, "JPEG", quality=90)
    return out.getvalue()
