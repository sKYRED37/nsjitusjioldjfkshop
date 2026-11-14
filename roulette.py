import io, os, random
from PIL import Image, ImageDraw, ImageFont
from aiogram.types import BufferedInputFile

def _roulette_gif(case_name: str, tag_names: list[str], winner: str) -> BufferedInputFile:
    """Рисует GIF-рулетку из всего пула кейса; последний кадр — реальный результат БД."""
    width, height = 800, 420
    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        title_font = ImageFont.truetype(bold_path, 30)
        tag_font = ImageFont.truetype(bold_path, 42)
        small_font = ImageFont.truetype(font_path, 19)
    except OSError:
        title_font = tag_font = small_font = ImageFont.load_default()

    pool = list(dict.fromkeys(str(x).strip() for x in tag_names if str(x).strip()))
    if winner not in pool:
        pool.append(winner)
    # Каждый тег из пула обязательно появляется хотя бы раз до финального кадра.
    sequence = pool[:]
    random.shuffle(sequence)
    target_frames = max(18, len(pool) + 8)
    while len(sequence) < target_frames - 1:
        sequence.append(random.choice(pool))
    random.shuffle(sequence)
    sequence = sequence[:target_frames - 1] + [winner]

    frames = []
    for index, name in enumerate(sequence):
        final = index == len(sequence) - 1
        im = Image.new("RGB", (width, height), (10, 13, 24))
        draw = ImageDraw.Draw(im)
        # Неоновая рамка и центральная карточка.
        accent = (255, 190, 72) if final else (74, 155, 255)
        draw.rounded_rectangle((22, 22, width-22, height-22), radius=28,
                               outline=accent, width=5)
        draw.text((width//2, 66), "КЕЙС  ·  " + case_name[:34], font=title_font,
                  fill=(235, 239, 248), anchor="mm")
        draw.text((width//2, 135), "ВЫПАДАЮЩИЙ ТЕГ", font=small_font,
                  fill=(160, 174, 198), anchor="mm")
        draw.rounded_rectangle((72, 176, width-72, 294), radius=22,
                               fill=(23, 31, 51), outline=accent, width=4)
        # Уменьшаем шрифт длинных названий, чтобы текст не обрезался.
        f = tag_font
        while f.size > 20 and draw.textbbox((0, 0), name, font=f)[2] > width - 180:
            f = ImageFont.truetype(bold_path, f.size - 2) if os.path.exists(bold_path) else small_font
        draw.text((width//2, 235), name, font=f, fill=(255, 255, 255), anchor="mm")
        draw.text((width//2, 348), "РЕЗУЛЬТАТ ЗАФИКСИРОВАН" if final else "РУЛЕТКА КРУТИТСЯ...",
                  font=small_font, fill=accent, anchor="mm")
        frames.append(im.quantize(colors=128))

    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:],
                   duration=[105] * (len(frames)-1) + [1900], loop=0, optimize=False)
    output.seek(0)
    return BufferedInputFile(output.read(), filename="case_roulette.gif")

