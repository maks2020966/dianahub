"""
Telegram-бот "Помощник по домашке" с новым дизайном
"""

import logging
import os
import uuid
import base64
import re
from io import BytesIO
import requests
from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==================== НАСТРОЙКИ ====================

TELEGRAM_TOKEN = "8983539613:AAFQLnAyx6KETqIyJBgBQk48IZg96CiXiA4"
GROQ_API_KEY = "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"

# ID стикера, который бот отправляет, пока думает.
# Чтобы получить ID нужного стикера, перешли его боту @idstickerbot в Telegram.
THINKING_STICKER_ID = "CAACAgIAAxkBAAER0GRqlTTPt9_45o-7jz-1uWlpGqz9BwACswgAAtdXGgdDqCNw-LSLGj0E"  # <-- ВСТАВЬ СЮДА ID СТИКЕРА

INTRO_VIDEO_PATH = "diana_hub_intro.mp4"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-20b" 
GROQ_VISION_MODEL = "qwen/qwen3.6-27b" 

# ---- Настройки картинки с ответом ----
IMG_WIDTH = 900
IMG_PADDING = 40
IMG_FONT_SIZE = 24
IMG_TITLE_FONT_SIZE = 28
IMG_SMALL_FONT_SIZE = 20

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_REGULAR = os.path.join(SCRIPT_DIR, "fonts", "DejaVuSans.ttf")
FONT_BOLD = os.path.join(SCRIPT_DIR, "fonts", "DejaVuSans-Bold.ttf")
TMP_DIR = "tmp_answers"

# Жесткий промпт, чтобы AI возвращал структурированный текст
SYSTEM_PROMPT = (
    "Ты — умный школьный помощник. Решай задачи по шагам. "
    "Твой ответ СТРОГО должен быть в таком формате (без markdown разметки типа **):\n"
    "ПРЕДМЕТ: [Название предмета и класс, например: МАТЕМАТИКА • НАЧАЛЬНАЯ ШКОЛА (1-4 КЛАССЫ)]\n"
    "УСЛОВИЕ: [Кратко перепиши условие задачи]\n"
    "ШАГИ:\n"
    "Шаг 1: [описание шага]\n"
    "Шаг 2: [описание шага, если нужно]\n"
    "ОТВЕТ: [Краткий итоговый ответ]\n\n"
    "Никогда не отклоняйся от этого шаблона."
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== ЗАПРОС К AI ====================

def ask_groq(question: str) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}],
        "temperature": 0.3,
        "max_tokens": 1500,
    }
    try:
        response = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Ошибка запроса к Groq: {e}")
        return "⚠️ Ошибка API."

def ask_groq_vision(image_url: str, caption: str = "") -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    user_text = caption.strip() if caption.strip() else "Реши это задание."
    payload = {
        "model": GROQ_VISION_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [{"type": "text", "text": user_text}, {"type": "image_url", "image_url": {"url": image_url}}]},
        ],
        "temperature": 0.3,
        "max_tokens": 1500,
    }
    try:
        response = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Ошибка запроса к Groq (vision): {e}")
        return "⚠️ Ошибка API."

# ==================== РЕНДЕР ОТВЕТА В КАРТИНКУ ====================

def wrap_text(text: str, font, max_width: int, draw: ImageDraw.Draw) -> list[str]:
    """Разбивает текст на строки по ширине."""
    lines = []
    for paragraph in text.split('\n'):
        words = paragraph.split(' ')
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip()
            if draw.textbbox((0, 0), test_line, font=font)[2] <= max_width:
                current_line = test_line
            else:
                if current_line: lines.append(current_line)
                current_line = word
        if current_line: lines.append(current_line)
    return lines

def render_styled_answer(answer_text: str) -> str:
    """Отрисовывает ответ по шаблону 2 фото на одной длинной картинке."""
    os.makedirs(TMP_DIR, exist_ok=True)
    
    # Пытаемся распарсить ответ
    subject = re.search(r"ПРЕДМЕТ:\s*(.*)", answer_text, re.IGNORECASE)
    condition = re.search(r"УСЛОВИЕ:\s*(.*?)(?=ШАГИ:)", answer_text, re.IGNORECASE | re.DOTALL)
    steps = re.search(r"ШАГИ:\s*(.*?)(?=ОТВЕТ:)", answer_text, re.IGNORECASE | re.DOTALL)
    final_answer = re.search(r"ОТВЕТ:\s*(.*)", answer_text, re.IGNORECASE | re.DOTALL)

    sub_text = subject.group(1).strip().upper() if subject else "ПРЕДМЕТ НЕ ОПРЕДЕЛЕН"
    cond_text = condition.group(1).strip() if condition else "Нет условия"
    steps_text = steps.group(1).strip() if steps else answer_text
    ans_text = final_answer.group(1).strip() if final_answer else ""

    try:
        font_reg = ImageFont.truetype(FONT_REGULAR, IMG_FONT_SIZE)
        font_bold = ImageFont.truetype(FONT_BOLD, IMG_FONT_SIZE)
        font_title = ImageFont.truetype(FONT_BOLD, IMG_TITLE_FONT_SIZE)
    except:
        font_reg = font_bold = font_title = ImageFont.load_default()

    # Временный холст для расчетов высоты
    tmp_img = Image.new("RGB", (10, 10))
    tmp_draw = ImageDraw.Draw(tmp_img)
    max_text_width = IMG_WIDTH - (IMG_PADDING * 4) # Отступы внутри блоков

    # Оборачиваем тексты
    cond_lines = wrap_text(cond_text, font_reg, max_text_width, tmp_draw)
    steps_lines = wrap_text(steps_text, font_reg, max_text_width - 20, tmp_draw) # -20 для синей полоски
    ans_lines = wrap_text(ans_text, font_reg, max_text_width, tmp_draw)

    line_height = tmp_draw.textbbox((0, 0), "Ay", font=font_reg)[3]
    
    # Считаем общую высоту картинки
    h_header = 100
    h_cond = len(cond_lines) * (line_height + 5) + 60
    h_steps = len(steps_lines) * (line_height + 5) + 40
    h_ans = len(ans_lines) * (line_height + 5) + 60 if ans_text else 0
    total_height = h_header + h_cond + h_steps + h_ans + 150

    img = Image.new("RGB", (IMG_WIDTH, total_height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    current_y = 40

    # 1. ЗАГОЛОВОК
    draw.text((IMG_PADDING, current_y), sub_text, font=font_title, fill=(59, 130, 246))
    draw.text((IMG_WIDTH - IMG_PADDING - 120, current_y + 8), "@dianahub", font=font_reg, fill=(150, 150, 150))
    current_y += 45
    draw.line([(IMG_PADDING, current_y), (IMG_WIDTH - IMG_PADDING, current_y)], fill=(59, 130, 246), width=3)
    current_y += 30

    # 2. УСЛОВИЕ (Голубой блок)
    cond_box_h = len(cond_lines) * (line_height + 5) + 50
    draw.rounded_rectangle(
        [(IMG_PADDING, current_y), (IMG_WIDTH - IMG_PADDING, current_y + cond_box_h)], 
        radius=15, fill=(240, 247, 255)
    )
    draw.text((IMG_PADDING + 20, current_y + 15), "Условие:", font=font_bold, fill=(30, 30, 30))
    cy = current_y + 45
    for line in cond_lines:
        draw.text((IMG_PADDING + 20, cy), line, font=font_reg, fill=(30, 30, 30))
        cy += line_height + 5
    current_y += cond_box_h + 30

    # 3. ШАГИ РЕШЕНИЯ (с синей полосой слева)
    # Рисуем вертикальную полосу
    steps_box_h = len(steps_lines) * (line_height + 5) + 20
    draw.line([(IMG_PADDING + 10, current_y), (IMG_PADDING + 10, current_y + steps_box_h)], fill=(59, 130, 246), width=4)
    cy = current_y
    for line in steps_lines:
        # Подсветка слов "Шаг X:"
        if line.lower().startswith("шаг"):
            draw.text((IMG_PADDING + 30, cy), line, font=font_bold, fill=(59, 130, 246))
        else:
            draw.text((IMG_PADDING + 30, cy), line, font=font_reg, fill=(50, 50, 50))
        cy += line_height + 5
    current_y += steps_box_h + 30

    # 4. ИТОГОВЫЙ ОТВЕТ (Зеленый блок с "пунктиром" - заменен на обводку для надежности)
    if ans_text:
        ans_box_h = len(ans_lines) * (line_height + 5) + 50
        # Зеленая обводка
        draw.rounded_rectangle(
            [(IMG_PADDING, current_y), (IMG_WIDTH - IMG_PADDING, current_y + ans_box_h)], 
            radius=10, fill=(255, 255, 255), outline=(76, 175, 80), width=3
        )
        draw.text((IMG_PADDING + 20, current_y + 15), "Итоговый ответ: ", font=font_bold, fill=(30, 30, 30))
        cy = current_y + 45
        for line in ans_lines:
            draw.text((IMG_PADDING + 20, cy), line, font=font_reg, fill=(30, 30, 30))
            cy += line_height + 5

    # Обрезка пустой нижней части (если расчитали с запасом)
    final_img = img.crop((0, 0, IMG_WIDTH, current_y + ans_box_h + 40))
    
    path = os.path.join(TMP_DIR, f"answer_{uuid.uuid4().hex}.png")
    final_img.save(path)
    return path

async def send_answer_as_photos(update: Update, answer: str, sticker_msg=None):
    try:
        image_path = render_styled_answer(answer)
        with open(image_path, "rb") as photo_file:
            await update.message.reply_photo(photo=photo_file)
        os.remove(image_path)
    except Exception as e:
        logger.error(f"Ошибка рендера: {e}")
        await update.message.reply_text(answer)
    finally:
        # Удаляем стикер "Думаю", когда ответ готов
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except:
                pass

# ==================== ХЕНДЛЕРЫ TELEGRAM ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(INTRO_VIDEO_PATH):
        try:
            with open(INTRO_VIDEO_PATH, "rb") as video_file:
                await update.message.reply_video(video=video_file)
        except Exception as e:
            pass
    await update.message.reply_text("Привет 👋\nОтправь фото или текст задания!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Отправляем стикер "Думаю..."
    sticker_msg = None
    try:
        sticker_msg = await update.message.reply_sticker(THINKING_STICKER_ID)
    except:
        pass # Если ID неверный, просто проигнорируем

    question = update.message.text
    answer = ask_groq(question)
    await send_answer_as_photos(update, answer, sticker_msg)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sticker_msg = None
    try:
        sticker_msg = await update.message.reply_sticker(THINKING_STICKER_ID)
    except:
        pass

    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)

    buf = BytesIO()
    await tg_file.download_to_memory(buf)
    image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    image_url = f"data:image/jpeg;base64,{image_b64}"

    caption = update.message.caption or ""
    answer = ask_groq_vision(image_url, caption)
    await send_answer_as_photos(update, answer, sticker_msg)

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()