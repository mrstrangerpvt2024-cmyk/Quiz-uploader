from quizbot.runner.handlers import quiz_handler
import os
import re
import asyncio
import requests
from threading import Thread
from io import BytesIO

from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.enums import PollType
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# FLASK SERVER FOR RAILWAY / RENDER
# ============================================================

web_app = Flask(__name__)

@web_app.route("/")
def health_check():
    return "Quiz Bot Active & Running!", 200

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

Thread(target=run_web_server, daemon=True).start()


# ============================================================
# BOT CONFIGURATION
# ============================================================

API_ID = int(os.environ.get("API_ID", "12345678"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")

app = Client(
    "quiz_uploader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

USER_CHAT_CONFIG = {}
USER_ACTIVE_FILES = {}
STOP_TASKS = {}
AWAITING_CHAT_ID = set()


# ============================================================
# HINDI FONT AUTO-DOWNLOADER
# ============================================================

FONT_PATH = "NotoSansDevanagari.ttf"

def get_hindi_font(size: int):
    if not os.path.exists(FONT_PATH):
        try:
            url = "https://github.com/google/fonts/raw/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                with open(FONT_PATH, "wb") as f:
                    f.write(r.content)
        except Exception as e:
            print(f"Font download error: {e}")

    if os.path.exists(FONT_PATH):
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception:
            pass

    return ImageFont.load_default()


# ============================================================
# DARK MODE IMAGE GENERATOR (UI CARD)
# ============================================================

def wrap_text(draw, text, font, max_width):
    """Wrap text by actual pixel width so Hindi/English both fit."""
    words = text.split()
    if not words:
        return [""]

    lines = []
    current = ""

    for word in words:
        test = word if not current else current + " " + word
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines or [""]


def parse_matching_pairs(question_text: str):
    """
    Parse matching data such as:
    A. First - 2. Harrod-Domar, B. Second - 4. Mahalanobis...
    Also accepts ':' or 'тАУ' instead of '-'.
    """
    pattern = re.compile(
        r"(?:^|[,:]\s*)([A-D])\s*[\.\)]\s*(.*?)\s*[-тАУтАФ:]\s*"
        r"([1-9])\s*[\.\)]\s*(.*?)(?=,\s*[A-D]\s*[\.\)]|$)",
        re.IGNORECASE
    )

    pairs = []
    for m in pattern.finditer(question_text):
        left_letter = m.group(1).upper()
        left_text = m.group(2).strip()
        right_number = m.group(3)
        right_text = m.group(4).strip()

        if left_text and right_text:
            pairs.append((left_letter, left_text, right_number, right_text))

    return pairs


def generate_matching_quiz_card(q_num: int, question_text: str) -> BytesIO:
    """
    Creates a dark Telegram-style matching-question card with
    two columns, similar to the screenshot supplied by the user.
    """
    width = 1200

    bg_color = (15, 24, 34)
    card_color = (29, 41, 57)
    header_color = (49, 66, 88)
    border_color = (66, 84, 108)
    text_color = (245, 247, 250)
    muted_color = (205, 214, 226)
    accent_color = (80, 180, 250)

    question_font = get_hindi_font(27)
    header_font = get_hindi_font(25)
    cell_font = get_hindi_font(23)
    small_font = get_hindi_font(21)

    pairs = parse_matching_pairs(question_text)

    # If no A-D/1-4 pairs can be extracted, fall back to the normal card.
    if not pairs:
        return generate_dark_quiz_card(q_num, question_text)

    # Remove the pair data from the question heading.
    heading = re.sub(
        r"(?:^|[,:]\s*)([A-D])\s*[\.\)]\s*.*?[-тАУтАФ:]\s*[1-9]\s*[\.\)]\s*.*$",
        "",
        question_text,
        flags=re.IGNORECASE
    ).strip(" ,:-тАУтАФ")

    # In the user's TXT format the question may end immediately before A. ...
    if not heading:
        heading = "рд╕реВрдЪреА-I рдХреЛ рд╕реВрдЪреА-II рд╕реЗ рд╕реБрдореЗрд▓рд┐рдд рдХреАрдЬрд┐рдП:"

    heading_lines = wrap_text(draw := ImageDraw.Draw(Image.new("RGB", (1, 1))), heading,
                              question_font, width - 140)

    # Estimate table height from wrapped cells.
    row_heights = []
    for _, left, _, right in pairs:
        left_lines = wrap_text(draw, left, cell_font, 430)
        right_lines = wrap_text(draw, right, cell_font, 500)
        row_heights.append(max(len(left_lines), len(right_lines), 1) * 34 + 28)

    header_h = 78
    heading_h = max(70, len(heading_lines) * 40 + 35)
    footer_h = 75
    table_h = header_h + sum(row_heights)
    height = min(2600, max(650, 60 + heading_h + table_h + footer_h))

    image = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(image)

    # Main rounded card.
    draw.rounded_rectangle(
        [25, 25, width - 25, height - 25],
        radius=20,
        fill=card_color,
        outline=border_color,
        width=2
    )

    # Question number.
    draw.text(
        (55, 48),
        f"ЁЯУМ Question #{q_num}",
        fill=accent_color,
        font=get_hindi_font(29)
    )

    y = 100
    for line in heading_lines:
        draw.text((55, y), line, fill=text_color, font=question_font)
        y += 40

    y += 18

    # Table geometry.
    x0 = 50
    x1 = 590
    x2 = width - 50

    # Header.
    draw.rectangle([x0, y, x2, y + header_h], fill=header_color, outline=border_color, width=2)
    draw.line([(x1, y), (x1, y + header_h)], fill=border_color, width=2)

    draw.text(
        (x0 + 25, y + 15),
        "рд╕реВрдЪреА-I / List-I",
        fill=text_color,
        font=header_font
    )
    draw.text(
        (x1 + 25, y + 15),
        "рд╕реВрдЪреА-II / List-II",
        fill=text_color,
        font=header_font
    )

    y += header_h

    # Rows.
    for left_letter, left_text, right_number, right_text in pairs:
        left_lines = wrap_text(draw, left_text, cell_font, 430)
        right_lines = wrap_text(draw, right_text, cell_font, 500)

        row_h = max(len(left_lines), len(right_lines), 1) * 34 + 28

        draw.rectangle(
            [x0, y, x2, y + row_h],
            fill=card_color,
            outline=border_color,
            width=2
        )
        draw.line([(x1, y), (x1, y + row_h)], fill=border_color, width=2)

        # Left cell.
        draw.text(
            (x0 + 18, y + 12),
            f"{left_letter}.",
            fill=accent_color,
            font=cell_font
        )
        ty = y + 12
        for line in left_lines:
            draw.text((x0 + 62, ty), line, fill=text_color, font=cell_font)
            ty += 34

        # Right cell.
        draw.text(
            (x1 + 18, y + 12),
            f"{right_number}.",
            fill=accent_color,
            font=cell_font
        )
        ty = y + 12
        for line in right_lines:
            draw.text((x1 + 65, ty), line, fill=text_color, font=cell_font)
            ty += 34

        y += row_h

    # Footer.
    y += 22
    draw.text(
        (55, y),
        "рдиреАрдЪреЗ рджрд┐рдП рдЧрдП рд╡рд┐рдХрд▓реНрдкреЛрдВ рдореЗрдВ рд╕реЗ рд╕рд╣реА рд╕реБрдореЗрд▓рди рдЪреБрдиреЗрдВ ЁЯСЗ",
        fill=muted_color,
        font=small_font
    )

    output = BytesIO()
    output.name = f"question_{q_num}.png"
    image.save(output, format="PNG")
    output.seek(0)
    return output


def generate_dark_quiz_card(q_num: int, question_text: str) -> BytesIO:
    """Normal question card for long/non-matching questions."""
    width = 1000

    bg_color = (21, 30, 40)
    card_color = (29, 41, 57)
    border_color = (43, 60, 80)
    text_color = (245, 247, 250)
    title_color = (100, 180, 250)

    font = get_hindi_font(26)
    title_font = get_hindi_font(30)

    image = Image.new("RGB", (width, 500), color=bg_color)
    draw = ImageDraw.Draw(image)

    lines = wrap_text(draw, question_text, font, width - 120)
    line_height = 45
    calculated_height = 180 + (len(lines) * line_height)
    height = max(500, min(2500, calculated_height))

    image = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        [30, 30, width - 30, height - 30],
        radius=18,
        fill=card_color,
        outline=border_color,
        width=2
    )

    draw.text((60, 55), f"ЁЯУМ Question #{q_num}", fill=title_color, font=title_font)
    draw.line([(50, 105), (width - 50, 105)], fill=border_color, width=2)

    y_offset = 130
    for line in lines:
        draw.text((60, y_offset), line, fill=text_color, font=font)
        y_offset += line_height

    img_byte_arr = BytesIO()
    img_byte_arr.name = f"question_{q_num}.png"
    image.save(img_byte_arr, format="PNG")
    img_byte_arr.seek(0)

    return img_byte_arr


def should_make_image(question_text: str) -> bool:
    """Decide when the question should be rendered as an image."""
    if parse_matching_pairs(question_text):
        return True

    if len(question_text) > 140:
        return True

    keywords = [
        "рд╕реБрдореЗрд▓рд┐рдд", "рд╕реВрдЪреА", "рдХрдерди", "рдХрдердиреЛрдВ", "рдорд┐рд▓рд╛рдЗрдП", "рд╕рдореНрдорд┐рд▓рд┐рдд",
        "match", "statement", "list-i", "list-ii", "рд╕реВрдЪреА-i", "рд╕реВрдЪреА-ii",
        "рдирд┐рдореНрдирд▓рд┐рдЦрд┐рдд", "рдХрдердиреЛрдВ рдкрд░ рд╡рд┐рдЪрд╛рд░"
    ]

    q_lower = question_text.lower()
    return any(kw in q_lower for kw in keywords)


# ============================================================
# QUIZ PARSER
# ============================================================

def parse_quiz_file(file_content: str) -> list:
    quizzes = []
    answer_map = {"A": 0, "B": 1, "C": 2, "D": 3}

    for line_no, line in enumerate(file_content.splitlines(), 1):
        line = line.strip()

        if not line or "|" not in line:
            continue

        # Exactly four fields are expected:
        # Question | Options | Correct Answer | Explanation
        parts = [x.strip() for x in line.split("|", 3)]
        if len(parts) < 3:
            continue

        question = parts[0].strip()
        question = re.sub(
            r"^Q\s*\d+\.\s*",
            "",
            question,
            flags=re.IGNORECASE
        ).strip()

        option_text = parts[1].strip()

        option_matches = re.findall(
            r"(?:^|,\s*)([A-D])\)\s*(.*?)(?=,\s*[A-D]\)\s*|$)",
            option_text,
            flags=re.IGNORECASE
        )

        options = [
            option.strip()
            for letter, option in option_matches
            if option.strip()
        ]

        if len(options) != 4:
            print(f"Skipping line {line_no}: expected 4 options")
            continue

        raw_answer = parts[2].strip().upper()
        answer_match = re.search(r"[A-D]", raw_answer)

        if not answer_match:
            print(f"Skipping line {line_no}: invalid answer")
            continue

        correct_letter = answer_match.group(0)
        correct_option_id = answer_map[correct_letter]

        explanation = parts[3].strip()[:200] if len(parts) >= 4 else ""

        matching_pairs = parse_matching_pairs(question)

        quizzes.append({
            "question": question,
            "options": options,
            "correct_option_id": correct_option_id,
            "correct_answer": correct_letter,
            "explanation": explanation,
            "is_matching": bool(matching_pairs),
            "matching_pairs": matching_pairs
        })

    return quizzes


# ============================================================
# HANDLERS
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    saved_chat = USER_CHAT_CONFIG.get(user_id, "Not Set")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("тЪЩя╕П Save Chat ID", callback_data="btn_save_chat_id")]
    ])

    await message.reply_text(
        f"ЁЯСЛ **Welcome to Quiz Uploader Bot!**\n\n"
        f"ЁЯОп **Target Chat:** `{saved_chat}`\n\n"
        f"`.txt` file bhej kar `/quiz` press karein.",
        reply_markup=keyboard
    )


@app.on_callback_query(filters.regex("^btn_save_chat_id$"))
async def cb_save_chat(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    AWAITING_CHAT_ID.add(user_id)
    await callback_query.message.reply_text("тЬПя╕П Target Chat ID Bhejein:")
    await callback_query.answer()


@app.on_message(filters.text & ~filters.command(["start", "quiz", "stop"]))
async def handle_direct_chat_id_input(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in AWAITING_CHAT_ID:
        return

    try:
        chat_id_int = int(message.text.strip())
        USER_CHAT_CONFIG[user_id] = chat_id_int
        AWAITING_CHAT_ID.remove(user_id)
        await message.reply_text(f"тЬЕ **Chat ID Saved:** `{chat_id_int}`")
    except ValueError:
        await message.reply_text("тЭМ Numeric Chat ID bhejein.")


@app.on_message(filters.document)
async def handle_document_upload(client: Client, message: Message):
    user_id = message.from_user.id
    file_name = message.document.file_name or ""

    if not file_name.lower().endswith(".txt"):
        await message.reply_text("тЭМ Sirf `.txt` files allowed hain.")
        return

    USER_ACTIVE_FILES[user_id] = message
    target_chat = USER_CHAT_CONFIG.get(user_id)

    if not target_chat:
        await message.reply_text("тЪая╕П Target Chat ID set karein.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("ЁЯЪА Start Upload", callback_data="btn_trigger_quiz")]
    ])

    await message.reply_text(
        f"ЁЯУД **File:** `{file_name}`\nЁЯОп **Chat:** `{target_chat}`",
        reply_markup=keyboard
    )


@app.on_message(filters.command("quiz"))
@app.on_callback_query(filters.regex("^btn_trigger_quiz$"))
async def start_quiz_process(client: Client, union_obj):
    if isinstance(union_obj, CallbackQuery):
        message = union_obj.message
        user_id = union_obj.from_user.id
        await union_obj.answer()
    else:
        message = union_obj
        user_id = union_obj.from_user.id

    target_chat = USER_CHAT_CONFIG.get(user_id)
    doc_message = USER_ACTIVE_FILES.get(user_id)

    if not target_chat or not doc_message:
        await message.reply_text("тЭМ Chat ID ya File missing hai.")
        return

    status_msg = await message.reply_text("ЁЯУе Processing File...")
    file_path = None

    try:
        file_path = await doc_message.download()
        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        quizzes = parse_quiz_file(content)
        if not quizzes:
            await status_msg.edit_text("тЭМ Valid quiz format nahi mila.")
            return

        target_chat_id = int(target_chat)
        await status_msg.edit_text(f"ЁЯЪА Upload Started! Total Questions: `{len(quizzes)}`")

        STOP_TASKS[user_id] = True
        success_count = 0

        for idx, q in enumerate(quizzes, start=1):
            if not STOP_TASKS.get(user_id, False):
                await status_msg.edit_text("ЁЯЫС Process Stopped.")
                break

            try:
                correct_id = int(q["correct_option_id"])
                full_question = q["question"]

                if q.get("is_matching", False):
                    img_stream = generate_matching_quiz_card(idx, full_question)
                    await client.send_photo(
                        chat_id=target_chat_id,
                        photo=img_stream,
                        caption=(
                            f"ЁЯУМ **Question #{idx}**\n\n"
                            "рдиреАрдЪреЗ рджрд┐рдП рдЧрдП рд╡рд┐рдХрд▓реНрдкреЛрдВ рдореЗрдВ рд╕реЗ рд╕рд╣реА рд╕реБрдореЗрд▓рди рдЪреБрдиреЗрдВ ЁЯСЗ"
                        )
                    )
                    poll_question = f"Question #{idx}: рд╕рд╣реА рд╕реБрдореЗрд▓рди рдЪреБрдиреЗрдВ:"
                elif should_make_image(full_question):
                    img_stream = generate_dark_quiz_card(idx, full_question)
                    await client.send_photo(
                        chat_id=target_chat_id,
                        photo=img_stream,
                        caption=(
                            f"ЁЯУМ **Question #{idx}**\n\n"
                            "рд╕рд╣реА рдЙрддреНрддрд░ рдиреАрдЪреЗ рдЪреБрдиреЗрдВ ЁЯСЗ"
                        )
                    )
                    poll_question = f"Question #{idx}: рд╕рд╣реА option рдЪреБрдиреЗрдВ:"
                else:
                    poll_question = f"{idx}. {full_question}"[:300]

                await client.send_poll(
                    chat_id=target_chat_id,
                    question=poll_question,
                    options=q["options"],
                    type=PollType.QUIZ,
                    correct_option_id=correct_id,
                    explanation=q["explanation"],
                    is_anonymous=True
                )

                success_count += 1
                await asyncio.sleep(2.5)

            except Exception as e:
                print(f"Upload Error Q{idx}: {e}")

        await status_msg.edit_text(f"тЬЕ Upload Complete! Total Posted: **{success_count}/{len(quizzes)}**")

    except Exception as e:
        await status_msg.edit_text(f"тЭМ Error: `{e}`")

    finally:
        STOP_TASKS.pop(user_id, None)
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


@app.on_message(filters.command("stop"))
async def stop_quiz_process(client: Client, message: Message):
    user_id = message.from_user.id
    if STOP_TASKS.get(user_id):
        STOP_TASKS[user_id] = False
        await message.reply_text("ЁЯЫС **Stop Signal Sent!**")


if __name__ == "__main__":
    app.run()
