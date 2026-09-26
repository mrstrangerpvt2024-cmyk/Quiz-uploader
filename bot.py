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
# FLASK SERVER FOR RENDER / RAILWAY
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
# HELPER: DOWNLOAD HINDI FONT FOR RAILWAY
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
# DARK MODE TABLE CARD GENERATOR (Screenshot Style)
# ============================================================

def generate_dark_quiz_card(q_num: int, question_text: str) -> BytesIO:
    width = 1000
    
    # Colors matching Telegram Dark Theme
    bg_color = (21, 30, 40)        # Deep Dark Background
    card_color = (29, 41, 57)      # Inner Card Color
    border_color = (43, 60, 80)    # Border Color
    text_color = (245, 247, 250)   # White/Light Text
    title_color = (100, 180, 250)  # Accent Color
    
    font = get_hindi_font(26)
    title_font = get_hindi_font(30)

    # Word Wrap Logic
    max_chars_per_line = 45
    words = question_text.split()
    lines = []
    current_line = []

    for word in words:
        current_line.append(word)
        if len(" ".join(current_line)) > max_chars_per_line:
            current_line.pop()
            lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))

    line_height = 45
    calculated_height = 180 + (len(lines) * line_height)
    height = max(500, min(2500, calculated_height))

    image = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(image)

    # Main Outer Rounded Card
    draw.rounded_rectangle(
        [30, 30, width - 30, height - 30],
        radius=18,
        fill=card_color,
        outline=border_color,
        width=2
    )

    # Title Line
    draw.text((60, 55), f"📌 Question #{q_num}", fill=title_color, font=title_font)
    draw.line([(50, 105), (width - 50, 105)], fill=border_color, width=2)

    # Question Content Rendering
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
    if len(question_text) > 150:
        return True
    
    keywords = [
        "सुमेलित", "सूची", "कथन", "कथनों", "मिलाइए", "सम्मिलित",
        "match", "statement", "list-i", "list-ii", "सूची-i", "सूची-ii",
        "निम्नलिखित", "कथनों पर विचार"
    ]
    
    q_lower = question_text.lower()
    for kw in keywords:
        if kw in q_lower:
            return True

    if re.search(r"\b[1-4]\.\s", question_text) or re.search(r"\b[A-D]\.\s", question_text):
        return True

    return False


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

        parts = [x.strip() for x in line.split("|", 3)]
        if len(parts) < 3:
            continue

        question = parts[0].strip()
        question = re.sub(r"^Q\s*\d+\.\s*", "", question, flags=re.IGNORECASE).strip()

        option_text = parts[1].strip()
        option_matches = re.findall(
            r"(?:^|,\s*)([A-D])\)\s*(.*?)(?=,\s*[A-D]\)\s*|$)",
            option_text,
            flags=re.IGNORECASE
        )

        options = [option.strip() for letter, option in option_matches if option.strip()]
        if len(options) != 4:
            continue

        raw_answer = parts[2].strip().upper()
        answer_match = re.search(r"[A-D]", raw_answer)
        if not answer_match:
            continue

        correct_letter = answer_match.group(0)
        correct_option_id = answer_map[correct_letter]

        explanation = parts[3].strip()[:200] if len(parts) >= 4 else ""

        quizzes.append({
            "question": question,
            "options": options,
            "correct_option_id": correct_option_id,
            "correct_answer": correct_letter,
            "explanation": explanation
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
        [InlineKeyboardButton("⚙️ Save Chat ID", callback_data="btn_save_chat_id")]
    ])

    await message.reply_text(
        f"👋 **Welcome to Advance Quiz Uploader Bot!**\n\n"
        f"🎯 **Target Chat:** `{saved_chat}`\n\n"
        f"`.txt` file bhej kar `/quiz` start karein.",
        reply_markup=keyboard
    )


@app.on_callback_query(filters.regex("^btn_save_chat_id$"))
async def cb_save_chat(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    AWAITING_CHAT_ID.add(user_id)
    await callback_query.message.reply_text("✏️ Target Chat ID Bhejein:")
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
        await message.reply_text(f"✅ **Chat ID Saved:** `{chat_id_int}`")
    except ValueError:
        await message.reply_text("❌ Numeric Chat ID bhejein.")


@app.on_message(filters.document)
async def handle_document_upload(client: Client, message: Message):
    user_id = message.from_user.id
    file_name = message.document.file_name or ""

    if not file_name.lower().endswith(".txt"):
        await message.reply_text("❌ Sirf `.txt` files allowed hain.")
        return

    USER_ACTIVE_FILES[user_id] = message
    target_chat = USER_CHAT_CONFIG.get(user_id)

    if not target_chat:
        await message.reply_text("⚠️ Target Chat ID set karein.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start Upload", callback_data="btn_trigger_quiz")]
    ])

    await message.reply_text(
        f"📄 **File:** `{file_name}`\n🎯 **Chat:** `{target_chat}`",
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
        await message.reply_text("❌ Chat ID ya File missing hai.")
        return

    status_msg = await message.reply_text("📥 Processing Quiz File...")
    file_path = None

    try:
        file_path = await doc_message.download()
        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        quizzes = parse_quiz_file(content)
        if not quizzes:
            await status_msg.edit_text("❌ Quiz parse nahi ho paaye.")
            return

        target_chat_id = int(target_chat)
        await status_msg.edit_text(f"🚀 Upload Started! Total: `{len(quizzes)}`")

        STOP_TASKS[user_id] = True
        success_count = 0

        for idx, q in enumerate(quizzes, start=1):
            if not STOP_TASKS.get(user_id, False):
                await status_msg.edit_text("🛑 Process Stopped.")
                break

            try:
                correct_id = int(q["correct_option_id"])
                full_question = q["question"]

                if should_make_image(full_question):
                    # Generate Dark Mode Card
                    img_stream = generate_dark_quiz_card(idx, full_question)
                    await client.send_photo(
                        chat_id=target_chat_id,
                        photo=img_stream,
                        caption=f"📌 **Question #{idx}**\n\nSahi uttar niche chunein 👇"
                    )
                    poll_question = f"Question #{idx}: Isme right answer konsa hoga?"
                else:
                    poll_question = f"{idx}. {full_question}"[:300]

                # Send Poll
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
                print(f"Error Q{idx}: {e}")

        await status_msg.edit_text(f"✅ Upload Complete! Posted: **{success_count}/{len(quizzes)}**")

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: `{e}`")

    finally:
        STOP_TASKS.pop(user_id, None)
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


@app.on_message(filters.command("stop"))
async def stop_quiz_process(client: Client, message: Message):
    user_id = message.from_user.id
    if STOP_TASKS.get(user_id):
        STOP_TASKS[user_id] = False
        await message.reply_text("🛑 **Stop Signal Sent!**")


if __name__ == "__main__":
    app.run()
