import os
import re
import asyncio
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

# Image Generation Library (PIL)
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# FLASK SERVER FOR RENDER
# ============================================================

web_app = Flask(__name__)


@web_app.route("/")
def health_check():
    return "Quiz Bot Active & Running!", 200


def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(
        host="0.0.0.0",
        port=port
    )


Thread(
    target=run_web_server,
    daemon=True
).start()


# ============================================================
# BOT CONFIGURATION
# ============================================================

API_ID = int(
    os.environ.get(
        "API_ID",
        "12345678"
    )
)

API_HASH = os.environ.get(
    "API_HASH",
    "your_api_hash"
)

BOT_TOKEN = os.environ.get(
    "BOT_TOKEN",
    "your_bot_token"
)


app = Client(
    "quiz_uploader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


# ============================================================
# MEMORY STORAGE
# ============================================================

USER_CHAT_CONFIG = {}
USER_ACTIVE_FILES = {}
STOP_TASKS = {}
AWAITING_CHAT_ID = set()


# ============================================================
# HELPER: IMAGE GENERATOR FOR LONG / COMPLEX QUESTIONS
# ============================================================

def generate_question_image(q_num: int, question_text: str) -> BytesIO:

    # Dynamic Height Calculation according to text length
    width = 1080
    base_height = 400
    
    # Text wrapping logic for body question
    max_chars_per_line = 50
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

    line_height = 42
    calculated_height = base_height + (len(lines) * line_height)
    height = max(600, min(1400, calculated_height))

    background_color = (245, 247, 250)
    card_color = (255, 255, 255)
    text_color = (33, 37, 41)
    header_color = (13, 110, 253)

    image = Image.new("RGB", (width, height), color=background_color)
    draw = ImageDraw.Draw(image)

    # Rounded rectangle background card
    draw.rounded_rectangle(
        [30, 30, width - 30, height - 30],
        radius=20,
        fill=card_color,
        outline=(222, 226, 230),
        width=3
    )

    # Load default fonts
    try:
        header_font = ImageFont.truetype("arial.ttf", 36)
        body_font = ImageFont.truetype("arial.ttf", 28)
    except IOError:
        header_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    # Draw Header / Title
    header_text = f"Question #{q_num}"
    draw.text((70, 60), header_text, fill=header_color, font=header_font)

    # Render Question Text
    y_offset = 130
    for line in lines:
        if y_offset > height - 80:
            draw.text((70, y_offset), "...", fill=text_color, font=body_font)
            break
        draw.text((70, y_offset), line, fill=text_color, font=body_font)
        y_offset += line_height

    # Save to buffer
    img_byte_arr = BytesIO()
    img_byte_arr.name = f"question_{q_num}.png"
    image.save(img_byte_arr, format="PNG")
    img_byte_arr.seek(0)

    return img_byte_arr


# Check if question requires Image Generation
def should_make_image(question_text: str) -> bool:
    # Length check (telegram limit safety)
    if len(question_text) > 200:
        return True
    
    # Keywords check for Sumelit / Matching / Statements
    image_keywords = [
        "सुमेलित", "सूची", "कथन", "कथनों", "मिलाइए", "सम्मिलित",
        "match", "statement", "list-i", "list-ii", "सूची-i", "सूची-ii"
    ]
    
    q_lower = question_text.lower()
    for kw in image_keywords:
        if kw in q_lower:
            return True
            
    return False


# ============================================================
# QUIZ PARSER
# ============================================================

def parse_quiz_file(file_content: str) -> list:

    quizzes = []

    answer_map = {
        "A": 0,
        "B": 1,
        "C": 2,
        "D": 3
    }

    for line_no, line in enumerate(
        file_content.splitlines(),
        1
    ):

        line = line.strip()

        if not line or "|" not in line:
            continue

        parts = [
            x.strip()
            for x in line.split("|", 3)
        ]

        if len(parts) < 3:
            print(f"[SKIP Q{line_no}] Invalid pipe format")
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

        options = []
        for letter, option in option_matches:
            option = option.strip()
            if option:
                options.append(option)

        if len(options) != 4:
            print(f"[SKIP Q{line_no}] Expected 4 options, found {len(options)}")
            continue

        raw_answer = parts[2].strip().upper()
        answer_match = re.search(r"[A-D]", raw_answer)

        if not answer_match:
            print(f"[SKIP Q{line_no}] Invalid answer: {raw_answer}")
            continue

        correct_letter = answer_match.group(0)
        correct_option_id = answer_map[correct_letter]

        explanation = ""
        if len(parts) >= 4:
            explanation = parts[3].strip()

        explanation = explanation[:200]

        quiz_data = {
            "question": question,
            "options": options,
            "correct_option_id": correct_option_id,
            "correct_answer": correct_letter,
            "explanation": explanation
        }

        quizzes.append(quiz_data)

    return quizzes


# ============================================================
# /START
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):

    user_id = message.from_user.id
    saved_chat = USER_CHAT_CONFIG.get(user_id, "Not Set")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⚙️ Save Chat ID",
                callback_data="btn_save_chat_id"
            )
        ]
    ])

    await message.reply_text(
        f"👋 **Welcome to Quiz Uploader Bot!**\n\n"
        f"🎯 **Saved Target Chat ID:** `{saved_chat}`\n\n"
        f"📌 **Instructions:**\n"
        f"1. Chat ID set karein.\n"
        f"2. `.txt` Quiz File bhejein.\n"
        f"3. Long & Sumelit / Statement vaale questions ki image automatically ban jayegi.\n"
        f"4. `/quiz` se upload start karein.",
        reply_markup=keyboard
    )


# ============================================================
# SAVE CHAT ID BUTTON & INPUT HANDLERS
# ============================================================

@app.on_callback_query(filters.regex("^btn_save_chat_id$"))
async def cb_save_chat(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    AWAITING_CHAT_ID.add(user_id)

    await callback_query.message.reply_text(
        "✏️ **Direct Target Chat ID Bhejein:**\n\n"
        "Example:\n`-1004399820534`"
    )
    await callback_query.answer()


@app.on_message(
    filters.text & ~filters.command(["start", "quiz", "stop"])
)
async def handle_direct_chat_id_input(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in AWAITING_CHAT_ID:
        return

    raw_input = message.text.strip()
    try:
        chat_id_int = int(raw_input)
        USER_CHAT_CONFIG[user_id] = chat_id_int
        AWAITING_CHAT_ID.remove(user_id)

        await message.reply_text(
            f"✅ **Chat ID Successfully Saved!**\n\n"
            f"🎯 **Target Chat ID:** `{chat_id_int}`"
        )
    except ValueError:
        await message.reply_text("❌ **Invalid Chat ID!** Numeric Chat ID bhejein.")


# ============================================================
# TXT FILE UPLOAD
# ============================================================

@app.on_message(filters.document)
async def handle_document_upload(client: Client, message: Message):
    user_id = message.from_user.id
    file_name = message.document.file_name or ""

    if not file_name.lower().endswith(".txt"):
        await message.reply_text("❌ Sirf `.txt` quiz files allowed hain.")
        return

    USER_ACTIVE_FILES[user_id] = message
    target_chat = USER_CHAT_CONFIG.get(user_id)

    if not target_chat:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚙️ Save Chat ID",
                    callback_data="btn_save_chat_id"
                )
            ]
        ])
        await message.reply_text(
            "⚠️ **Target Chat ID Set Nahi Hai!**",
            reply_markup=keyboard
        )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🚀 Start Upload",
                callback_data="btn_trigger_quiz"
            )
        ]
    ])

    await message.reply_text(
        f"📄 **File Received:** `{file_name}`\n"
        f"🎯 **Target Chat:** `{target_chat}`\n\n"
        f"🚀 Press `/quiz` or click button below.",
        reply_markup=keyboard
    )


# ============================================================
# QUIZ PROCESSOR & UPLOADER
# ============================================================

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
        await message.reply_text("❌ Target Chat ID ya TXT file missing hai.")
        return

    status_msg = await message.reply_text("📥 Processing Quiz File...")
    file_path = None

    try:
        file_path = await doc_message.download()

        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        quizzes = parse_quiz_file(content)

        if not quizzes:
            await status_msg.edit_text("❌ **No Valid Quiz Found!**")
            return

        target_chat_id = int(target_chat)
        await client.get_chat(target_chat_id)

        await status_msg.edit_text(
            f"🚀 **Quiz Upload Started!**\n\n"
            f"📊 Total Questions: `{len(quizzes)}`\n"
            f"🎯 Target: `{target_chat_id}`"
        )

        STOP_TASKS[user_id] = True
        success_count = 0
        failed_count = 0

        for idx, q in enumerate(quizzes, start=1):

            if not STOP_TASKS.get(user_id, False):
                await status_msg.edit_text("🛑 Upload Process Stopped!")
                break

            try:
                correct_id = int(q["correct_option_id"])
                full_question = q["question"]

                # CHECK: Dynamic Image logic for long questions OR Matching/Statement type
                if should_make_image(full_question):
                    img_stream = generate_question_image(idx, full_question)

                    # 1. Question image post karo
                    await client.send_photo(
                        chat_id=target_chat_id,
                        photo=img_stream,
                        caption=f"📌 **Question #{idx}**\n\nUpar image me diye question ko padhkar sahi option chunein 👇"
                    )

                    # 2. Options ke liye Quiz Poll bhejo
                    poll_question = f"Question #{idx}: Isme right answer konsa hoga?"
                    await client.send_poll(
                        chat_id=target_chat_id,
                        question=poll_question,
                        options=q["options"],
                        type=PollType.QUIZ,
                        correct_option_id=correct_id,
                        explanation=q["explanation"],
                        is_anonymous=True
                    )
                else:
                    # Normal Quiz Poll
                    poll_question = f"{idx}. {full_question}"
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
                failed_count += 1
                print(f"Error Q{idx}: {e}")

        await status_msg.edit_text(
            f"✅ **Upload Complete!**\n\n"
            f"📊 Uploaded: **{success_count}/{len(quizzes)}**\n"
            f"⚠️ Failed: **{failed_count}**"
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: `{e}`")

    finally:
        STOP_TASKS.pop(user_id, None)
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


# ============================================================
# /STOP
# ============================================================

@app.on_message(filters.command("stop"))
async def stop_quiz_process(client: Client, message: Message):
    user_id = message.from_user.id
    if STOP_TASKS.get(user_id):
        STOP_TASKS[user_id] = False
        await message.reply_text("🛑 **Stop Signal Sent!**")
    else:
        await message.reply_text("⚠️ Koi active upload nahi hai.")


if __name__ == "__main__":
    app.run()
