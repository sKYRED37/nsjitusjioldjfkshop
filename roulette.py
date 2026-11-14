import io
import random
from PIL import Image, ImageDraw, ImageFont
from aiogram.types import BufferedInputFile

FRAME_COUNT = 48
FINAL_HOLD_MS = 1700


def _unique_pool(tag_names: list[str], winner: str) -> list[str]:
    pool = list(dict.fromkeys(str(x).strip() for x in tag_names if str(x).strip()))
    if winner and winner not in pool:
        pool.append(winner)
    return pool or [winner or "Тег"]


def _frame_durations() -> list[int]:
    values = []
    for i in range(FRAME_COUNT - 1):
        t = i / (FRAME_COUNT - 2)
        values.append(int(70 + 145 * (t ** 2.2)))
    return values + [FINAL_HOLD_MS]


def _roulette_duration_seconds(tag_names: list[str], winner: str) -> float:
    return sum(_frame_durations()) / 1000.0


def _roulette_gif(case_name: str, tag_names: list[str], winner: str) -> BufferedInputFile:
    """Square high-resolution reel; designed for readable Telegram chat preview."""
    width = height = 1080
    regular_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def font(size: int):
        try:
            return ImageFont.truetype(bold_path, size)
        except OSError:
            return ImageFont.load_default()

    title_font = font(64)
    hint_font = font(38)
    card_font = font(88)
    result_font = font(54)
    pool = _unique_pool(tag_names, winner)

    # Every available tag is represented; the final slot is the DB-selected winner.
    track = [random.choice(pool) for _ in range(25)]
    for i, tag in enumerate(pool[:24]):
        track[i] = tag
    prefix = track[:24]
    random.shuffle(prefix)
    track[:24] = prefix
    track[24] = winner

    frames = []
    card_h, gap = 158, 26
    step = card_h + gap
    center_x = center_y = 540
    start_index = 14
    travel = 10.0

    for frame_index in range(FRAME_COUNT):
        t = frame_index / (FRAME_COUNT - 1)
        eased = 1 - (1 - t) ** 2.8
        offset = eased * travel
        final = frame_index == FRAME_COUNT - 1

        im = Image.new("RGB", (width, height), (5, 9, 22))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((14, 14, width - 14, height - 14), radius=54,
                            fill=(12, 20, 40), outline=(64, 91, 140), width=7)
        d.text((center_x, 92), "ОТКРЫТИЕ КЕЙСА", font=title_font,
               fill=(250, 252, 255), anchor="mm")
        case_label = str(case_name)
        while d.textbbox((0, 0), case_label, font=hint_font)[2] > 940 and hint_font.size > 24:
            hint_font = font(hint_font.size - 2)
        d.text((center_x, 165), case_label[:42], font=hint_font,
               fill=(184, 204, 238), anchor="mm")
        d.text((center_x, 235), "СМОТРИ, ЧТО ВЫПАДЕТ", font=hint_font,
               fill=(255, 205, 105), anchor="mm")

        d.rounded_rectangle((38, 285, width - 38, 805), radius=42,
                            fill=(3, 7, 17), outline=(68, 91, 132), width=5)
        for idx, tag in enumerate(track):
            y = center_y + (idx - start_index - offset) * step - card_h / 2
            if y < 285 - card_h or y > 805:
                continue
            is_center = abs((idx - start_index) - offset) < 0.52
            fill = (29, 45, 73) if not is_center else (49, 76, 119)
            outline = (76, 107, 160) if not is_center else (255, 199, 75)
            d.rounded_rectangle((66, int(y), width - 66, int(y + card_h)),
                                radius=30, fill=fill, outline=outline,
                                width=9 if is_center else 4)
            label = str(tag)
            f = card_font
            while f.size > 32 and d.textbbox((0, 0), label, font=f)[2] > 850:
                f = font(f.size - 4)
            d.text((center_x, int(y + card_h / 2)), label, font=f,
                   fill=(255, 255, 255), anchor="mm")

        # Thick fixed selection frame and visible pointers.
        d.rounded_rectangle((55, center_y - card_h / 2 - 12,
                             width - 55, center_y + card_h / 2 + 12),
                            radius=38, outline=(255, 199, 75), width=10)
        d.polygon([(center_x - 38, 268), (center_x + 38, 268), (center_x, 312)],
                  fill=(255, 199, 75))
        d.polygon([(center_x - 38, 822), (center_x + 38, 822), (center_x, 778)],
                  fill=(255, 199, 75))
        footer = "ВЫПАЛО!" if final else "РУЛЕТКА КРУТИТСЯ"
        d.text((center_x, 900), footer, font=title_font,
               fill=(255, 211, 120) if final else (180, 199, 231), anchor="mm")
        if final:
            d.rounded_rectangle((65, 950, width - 65, 1030), radius=24,
                                fill=(35, 55, 88), outline=(255, 199, 75), width=4)
            d.text((center_x, 990), str(winner)[:42], font=result_font,
                   fill=(255, 255, 255), anchor="mm")

        # Full 256-color palette keeps text and edges much sharper than the old 128-color GIF.
        frames.append(im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))

    output = io.BytesIO()
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=_frame_durations(), loop=1, optimize=False, disposal=2,
    )
    output.seek(0)
    return BufferedInputFile(output.read(), filename="case_roulette.gif")
