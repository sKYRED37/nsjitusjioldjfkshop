import io
import random
from PIL import Image, ImageDraw, ImageFont
from aiogram.types import BufferedInputFile

# 48 кадров с плавным замедлением и короткой фиксацией результата.
FRAME_COUNT = 48
FRAME_MS = 110
FINAL_HOLD_MS = 1700


def _unique_pool(tag_names: list[str], winner: str) -> list[str]:
    pool = list(dict.fromkeys(str(x).strip() for x in tag_names if str(x).strip()))
    if winner and winner not in pool:
        pool.append(winner)
    return pool or [winner or "Тег"]


def _frame_durations() -> list[int]:
    # Движение быстрое в начале и плавно затухает к последнему кадру.
    values = []
    for i in range(FRAME_COUNT - 1):
        t = i / (FRAME_COUNT - 2)
        values.append(int(65 + 145 * (t ** 2.2)))
    return values + [FINAL_HOLD_MS]


def _roulette_duration_seconds(tag_names: list[str], winner: str) -> float:
    # Pillow may coalesce identical adjacent GIF frames; this matches the
    # encoded animation duration (about 6.7 seconds) with a small safety margin.
    return 6.7


def _roulette_gif(case_name: str, tag_names: list[str], winner: str) -> BufferedInputFile:
    """Large portrait reel for Telegram mobile: tags move vertically through center."""
    width, height = 720, 1280
    regular_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def font(size: int, bold: bool = False):
        try:
            return ImageFont.truetype(bold_path if bold else regular_path, size)
        except OSError:
            return ImageFont.load_default()

    title_font = font(42, True)
    hint_font = font(30, True)
    card_font = font(58, True)
    small_font = font(32, True)
    pool = _unique_pool(tag_names, winner)

    # The winning tag is placed at the final center position. Every pool tag
    # is included in the reel, while the final frame is tied to DB's winner.
    track = [random.choice(pool) for _ in range(25)]
    for i, tag in enumerate(pool[:20]):
        track[i] = tag
    track[24] = winner
    prefix = track[:24]
    random.shuffle(prefix)
    track[:24] = prefix
    track[24] = winner

    frames = []
    card_w, card_h, gap = 620, 190, 34
    step = card_h + gap
    center_x = width // 2
    center_y = 640
    start_index = 14
    travel = 10.0

    for frame_index in range(FRAME_COUNT):
        t = frame_index / (FRAME_COUNT - 1)
        eased = 1 - (1 - t) ** 2.8
        offset = eased * travel
        final = frame_index == FRAME_COUNT - 1

        im = Image.new("RGB", (width, height), (7, 12, 27))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((12, 12, width - 12, height - 12), radius=34,
                            fill=(13, 21, 42), outline=(55, 78, 119), width=4)
        d.text((center_x, 100), "ОТКРЫТИЕ КЕЙСА", font=title_font,
               fill=(248, 250, 255), anchor="mm")
        d.text((center_x, 164), str(case_name)[:36], font=hint_font,
               fill=(170, 194, 229), anchor="mm")
        d.text((center_x, 240), "СЛЕДИ ЗА ВЫИГРЫШЕМ", font=hint_font,
               fill=(255, 199, 92), anchor="mm")

        # Tall reel window makes the animation occupy much more phone screen.
        d.rounded_rectangle((34, 300, width - 34, 1000), radius=30,
                            fill=(5, 9, 20), outline=(52, 72, 108), width=3)
        for idx, tag in enumerate(track):
            y = center_y + (idx - start_index - offset) * step - card_h / 2
            if y < 300 - card_h or y > 1000:
                continue
            is_center = abs((idx - start_index) - offset) < 0.52
            fill = (31, 47, 76) if not is_center else (53, 76, 116)
            outline = (72, 101, 151) if not is_center else (255, 193, 67)
            d.rounded_rectangle((50, int(y), width - 50, int(y + card_h)),
                                radius=28, fill=fill, outline=outline,
                                width=7 if is_center else 3)
            label = str(tag)
            f = card_font
            while f.size > 28 and d.textbbox((0, 0), label, font=f)[2] > card_w - 56:
                f = font(f.size - 3, True)
            d.text((center_x, int(y + card_h / 2)), label, font=f,
                   fill=(255, 255, 255), anchor="mm")

        # Fixed center markers show exactly which card is selected.
        d.rounded_rectangle((45, center_y - card_h / 2 - 10,
                             width - 45, center_y + card_h / 2 + 10),
                            radius=30, outline=(255, 193, 67), width=7)
        d.polygon([(center_x - 25, 286), (center_x + 25, 286), (center_x, 318)],
                  fill=(255, 193, 67))
        d.polygon([(center_x - 25, 1014), (center_x + 25, 1014), (center_x, 982)],
                  fill=(255, 193, 67))
        footer = "ТЕГ ВЫПАЛ!" if final else "РУЛЕТКА КРУТИТСЯ"
        d.text((center_x, 1110), footer, font=title_font,
               fill=(255, 207, 105) if final else (166, 190, 225), anchor="mm")
        if final:
            d.rounded_rectangle((42, 1160, width - 42, 1240), radius=22,
                                fill=(35, 54, 86), outline=(255, 193, 67), width=3)
            d.text((center_x, 1200), str(winner)[:40], font=small_font,
                   fill=(255, 255, 255), anchor="mm")
        frames.append(im.quantize(colors=128))

    output = io.BytesIO()
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=_frame_durations(), loop=1, optimize=True, disposal=2,
    )
    output.seek(0)
    return BufferedInputFile(output.read(), filename="case_roulette.gif")
