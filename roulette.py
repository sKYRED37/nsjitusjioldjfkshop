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
    """Animated horizontal tag reel; the center stop is always the database winner."""
    width, height = 960, 540
    regular_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def font(size: int, bold: bool = False):
        try:
            return ImageFont.truetype(bold_path if bold else regular_path, size)
        except OSError:
            return ImageFont.load_default()

    title_font = font(30, True)
    hint_font = font(21, True)
    card_font = font(36, True)
    small_font = font(18, True)
    pool = _unique_pool(tag_names, winner)

    # Позиция 24 — победитель в финальном кадре; все варианты пула
    # гарантированно попадают в видимую ленту во время прокрутки.
    track = [random.choice(pool) for _ in range(25)]
    for i, tag in enumerate(pool[:20]):
        track[i] = tag
    track[24] = winner
    prefix = track[:24]
    random.shuffle(prefix)
    track[:24] = prefix
    track[24] = winner

    frames = []
    card_w, card_h, gap = 224, 154, 18
    step = card_w + gap
    center_x = width // 2
    center_y = 300
    start_index = 14
    travel = 10.0

    for frame_index in range(FRAME_COUNT):
        t = frame_index / (FRAME_COUNT - 1)
        # Ease-out curve: fast start, gradually slower finish.
        eased = 1 - (1 - t) ** 2.8
        offset = eased * travel
        final = frame_index == FRAME_COUNT - 1

        im = Image.new("RGB", (width, height), (9, 14, 29))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((14, 14, width - 14, height - 14), radius=30,
                            fill=(13, 20, 39), outline=(48, 69, 105), width=3)
        d.text((center_x, 72), "ОТКРЫТИЕ КЕЙСА", font=title_font,
               fill=(245, 248, 255), anchor="mm")
        d.text((center_x, 116), str(case_name)[:42], font=hint_font,
               fill=(159, 183, 218), anchor="mm")

        # Reel background and side fades.
        d.rounded_rectangle((28, 178, width - 28, 424), radius=24,
                            fill=(7, 11, 23), outline=(44, 61, 91), width=2)

        # Draw nearby cards on a continuous moving track.
        for idx, tag in enumerate(track):
            x = center_x + (idx - start_index - offset) * step - card_w / 2
            if x < -card_w or x > width:
                continue
            y = center_y - card_h / 2
            is_center = abs((idx - start_index) - offset) < 0.52
            fill = (34, 49, 77) if not is_center else (46, 67, 103)
            outline = (65, 92, 137) if not is_center else (255, 190, 65)
            d.rounded_rectangle((int(x), y, int(x + card_w), y + card_h),
                                radius=22, fill=fill, outline=outline,
                                width=5 if is_center else 2)
            label = str(tag)
            f = card_font
            while f.size > 19 and d.textbbox((0, 0), label, font=f)[2] > card_w - 28:
                f = font(f.size - 2, True)
            d.text((int(x + card_w / 2), center_y), label, font=f,
                   fill=(255, 255, 255), anchor="mm")

        # Fixed center pointer and selection frame.
        pointer_x = center_x
        d.polygon([(pointer_x - 17, 165), (pointer_x + 17, 165), (pointer_x, 190)],
                  fill=(255, 190, 65))
        d.polygon([(pointer_x - 17, 437), (pointer_x + 17, 437), (pointer_x, 412)],
                  fill=(255, 190, 65))
        d.rounded_rectangle((center_x - 126, 194, center_x + 126, 406),
                            radius=24, outline=(255, 190, 65), width=4)

        footer = "ТЕГ ВЫПАЛ!" if final else "ПРОКРУТКА  •  ПРОКРУТКА  •  ПРОКРУТКА"
        footer_color = (255, 205, 93) if final else (145, 171, 211)
        d.text((center_x, 476), footer, font=hint_font,
               fill=footer_color, anchor="mm")
        if final:
            # Ensure the winning tag is visibly centered in the final frame.
            d.rounded_rectangle((center_x - 205, 492, center_x + 205, 526),
                                radius=12, fill=(37, 54, 83))
            d.text((center_x, 509), str(winner)[:48], font=small_font,
                   fill=(255, 255, 255), anchor="mm")

        frames.append(im.quantize(colors=128))

    output = io.BytesIO()
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=_frame_durations(), loop=1, optimize=True, disposal=2,
    )
    output.seek(0)
    return BufferedInputFile(output.read(), filename="case_roulette.gif")
