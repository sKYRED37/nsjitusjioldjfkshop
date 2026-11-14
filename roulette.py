import io, os, random
from PIL import Image, ImageDraw, ImageFont
from aiogram.types import BufferedInputFile

# GIF playback timings are shared with user.py so the result message never
# overtakes the animation in the Telegram chat.
FRAME_MS = 170
FINAL_HOLD_MS = 1800


def _unique_pool(tag_names: list[str], winner: str) -> list[str]:
    pool = list(dict.fromkeys(str(x).strip() for x in tag_names if str(x).strip()))
    if winner and winner not in pool:
        pool.append(winner)
    return pool or [winner or "Тег"]


def _roulette_duration_seconds(tag_names: list[str], winner: str) -> float:
    pool = _unique_pool(tag_names, winner)
    frame_count = max(32, len(pool) + 12)
    total_ms = (frame_count - 1) * FRAME_MS + FINAL_HOLD_MS
    return total_ms / 1000


def _roulette_gif(case_name: str, tag_names: list[str], winner: str) -> BufferedInputFile:
    """Большая 16:9 GIF: все теги пула прокручиваются, финал — победитель из БД."""
    width, height = 1280, 720
    regular_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def load_font(size: int, bold: bool = False):
        path = bold_path if bold else regular_path
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            return ImageFont.load_default()

    title_font = load_font(48, True)
    label_font = load_font(30, True)
    tag_font = load_font(82, True)
    footer_font = load_font(27, True)

    pool = _unique_pool(tag_names, winner)
    # Показываем каждый тег из пула хотя бы один раз, затем перемешиваем
    # дополнительные проходы. Победитель всегда строго последний.
    frame_count = max(32, len(pool) + 12)
    sequence = pool[:]
    while len(sequence) < frame_count - 1:
        sequence.append(random.choice(pool))
    random.shuffle(sequence)
    sequence = sequence[:frame_count - 1] + [winner]

    frames = []
    for index, name in enumerate(sequence):
        final = index == len(sequence) - 1
        im = Image.new("RGB", (width, height), (9, 13, 27))
        draw = ImageDraw.Draw(im)
        accent = (255, 190, 65) if final else (68, 157, 255)

        # Контрастная рамка и крупная центральная карточка для мобильного экрана.
        draw.rounded_rectangle((28, 28, width - 28, height - 28), radius=42,
                               outline=accent, width=8)
        draw.text((width // 2, 125), "КЕЙС  ·  " + str(case_name)[:38],
                  font=title_font, fill=(242, 245, 252), anchor="mm")
        draw.text((width // 2, 250), "ВЫПАДАЮЩИЙ ТЕГ", font=label_font,
                  fill=(164, 181, 207), anchor="mm")
        draw.rounded_rectangle((90, 310, width - 90, 505), radius=34,
                               fill=(23, 33, 57), outline=accent, width=7)

        # Автоматически уменьшаем шрифт только для очень длинных тегов.
        f = tag_font
        while f.size > 34 and draw.textbbox((0, 0), str(name), font=f)[2] > width - 250:
            f = load_font(f.size - 4, True)
        draw.text((width // 2, 407), str(name), font=f,
                  fill=(255, 255, 255), anchor="mm")
        footer = "🏆 ВЫПАЛ ЭТОТ ТЕГ" if final else "РУЛЕТКА КРУТИТСЯ  •  •  •"
        draw.text((width // 2, 590), footer, font=footer_font,
                  fill=accent, anchor="mm")
        frames.append(im.quantize(colors=128))

    output = io.BytesIO()
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=[FRAME_MS] * (len(frames) - 1) + [FINAL_HOLD_MS],
        loop=1, optimize=False, disposal=2,
    )
    output.seek(0)
    return BufferedInputFile(output.read(), filename="case_roulette.gif")
