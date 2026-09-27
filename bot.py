import os
import re
import asyncio
from threading import Thread
from flask import Flask

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.enums import PollType

# Pillow for Image Generation
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


# ============================================================
# MEMORY STORAGE
# ============================================================

USER_CHAT_CONFIG = {}
USER_ACTIVE_FILES = {}
STOP_TASKS = {}
AWAITING_CHAT_ID = set()


# ============================================================
# IMAGE GENERATOR FUNCTION
# ============================================================

def draw_wrapped_text(draw, text, font, x, y, max_width, fill_color):
    """टेक्स्ट को ऑटोमैटिकली अगली लाइन पर रैप करता है"""
    words = text.split(' ')
    lines = []
    current_line = []

    for word in words:
        current_line.append(word)
        bbox = draw.textbbox((0, 0), ' '.join(current_line), font=font)
        if bbox[2] - bbox[0] > max_width:
            current_line.pop()
            lines.append(' '.join(current_line))
            current_line = [word]
    lines.append(' '.join(current_line))

    current_y = y
    for line in lines:
        draw.text((x, current_y), line, font=font, fill=fill_color)
        bbox = draw.textbbox((0, 0), line, font=font)
        current_y += (bbox[3] - bbox[1]) + 8  # Line spacing

    return current_y


def create_question_image(q_num: int, question_text: str, options: list, output_path="quiz_image.png") -> str:
    """प्रश्नों को सुंदर डार्क-थीम वाली कार्ड इमेज में कन्वर्ट करता है"""
    BG_COLOR = (24, 34, 45)       # Telegram Dark Theme BG
    CARD_BG = (32, 44, 58)        # Card BG
    TEXT_COLOR = (245, 245, 245)   # Main Text
    ACCENT_COLOR = (74, 155, 255) # Header Accent
    OPTION_COLOR = (200, 220, 240)# Option Text
    
    WIDTH = 800
    PADDING = 25

    try:
        # Default font loader
        font_title = ImageFont.truetype("arial.ttf", 22)
        font_q = ImageFont.truetype("arial.ttf", 20)
        font_opt = ImageFont.truetype("arial.ttf", 18)
    except:
        font_title = font_q = font_opt = ImageFont.load_default()

    # ऊंचाई मापने के लिए डमी इमेज
    dummy_img = Image.new("RGB", (WIDTH, 2000), BG_COLOR)
    draw = ImageDraw.Draw(dummy_img)

    y = PADDING
    
    # हेडर (Question Number)
    draw.text((PADDING, y), f"Q {q_num}.", font=font_title, fill=ACCENT_COLOR)
    y += 35

    # सवाल
    y = draw_wrapped_text(draw, question_text, font_q, PADDING, y, WIDTH - (PADDING * 2), TEXT_COLOR)
    y += 20

    # ऑप्शंस बॉक्स कार्ड
    opt_labels = ["A", "B", "C", "D"]
    for i, opt in enumerate(options):
        opt_str = f"{opt_labels[i]}) {opt}"
        opt_start_y = y
        y = draw_wrapped_text(draw, opt_str, font_opt, PADDING + 15, y + 10, WIDTH - (PADDING * 2) - 30, OPTION_COLOR)
        opt_height = y - opt_start_y + 10
        
        # Option Box Outline
        draw.rectangle(
            [PADDING, opt_start_y, WIDTH - PADDING, opt_start_y + opt_height],
            fill=CARD_BG,
            outline=(50, 65, 82),
            width=1
        )
        # Re-draw text over rectangle
        draw_wrapped_text(draw, opt_str, font_opt, PADDING + 15, opt_start_y + 10, WIDTH - (PADDING * 2) - 30, OPTION_COLOR)
        y = opt_start_y + opt_height + 10

    total_height = y + PADDING

    # Final Image Crop & Save
    final_img = dummy_img.crop((0, 0, WIDTH, total_height))
    final_img.save(output_path)
    return output_path


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

        options = [opt.strip() for _, opt in option_matches if opt.strip()]
        if len(options) != 4:
            continue

        raw_answer = parts[2].strip().upper()
        answer_match = re.search(r"[A-D]", raw_answer)
        if not answer_match:
            continue

        correct_letter = answer_match.group(0)
        correct_option_id = answer_map[correct_letter]

        explanation = parts[3].strip() if len(parts) >= 4 else ""
        explanation = explanation[:200]

        quizzes.append({
            "question": question,
            "options": options,
            "correct_option_id": correct_option_id,
            "correct_answer": correct_letter,
            "explanation": explanation
        })

    return quizzes


# ============================================================
# /START & CHAT CONFIG HANDLERS
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    saved_chat = USER_CHAT_CONFIG.get(user_id, "Not Set")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Save Chat ID", callback_data="btn_save_chat_id")]
    ])

    await message.reply_text(
        f"👋 **Welcome to Quiz Image Uploader Bot!**\n\n"
        f"🎯 **Saved Target Chat ID:** `{saved_chat}`\n\n"
        f"📌 **Instructions:**\n"
        f"1. Chat ID set karein.\n"
        f"2. `.txt` Quiz File bhejein.\n"
        f"3. Bot automatic **Image Card** banakar quiz post karega!\n\n"
        f"Format:\n`Question | A) Opt1, B) Opt2, C) Opt3, D) Opt4 | B | Explanation`",
        reply_markup=keyboard
    )


@app.on_callback_query(filters.regex("^btn_save_chat_id$"))
async def cb_save_chat(client: Client, callback_query: CallbackQuery):
    AWAITING_CHAT_ID.add(callback_query.from_user.id)
    await callback_query.message.reply_text(
        "✏️ **Direct Target Chat ID Bhejein:**\n\nExample:\n`-1004399820534`"
    )
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
        await message.reply_text("❌ Invalid Chat ID! Numeric ID bhejein.")


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
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Save Chat ID", callback_data="btn_save_chat_id")]])
        await message.reply_text("⚠️ Target Chat ID Set Nahi Hai!", reply_markup=keyboard)
        return

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Start Upload", callback_data="btn_trigger_quiz")]])
    await message.reply_text(
        f"📄 **File Received:** `{file_name}`\n🎯 **Target Chat:** `{target_chat}`",
        reply_markup=keyboard
    )


# ============================================================
# QUIZ UPLOAD WITH IMAGE GENERATION
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
        await message.reply_text("❌ File ya Target Chat ID miss hai!")
        return

    status_msg = await message.reply_text("📥 Downloading & Generating Images...")
    file_path = None

    try:
        file_path = await doc_message.download()

        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        quizzes = parse_quiz_file(content)
        if not quizzes:
            await status_msg.edit_text("❌ No Valid Quizzes Found!")
            return

        target_chat_id = int(target_chat)
        STOP_TASKS[user_id] = True

        await status_msg.edit_text(
            f"🚀 **Quiz Image Upload Started!**\n\n"
            f"📊 Total Questions: `{len(quizzes)}`\n"
            f"🎯 Target: `{target_chat_id}`"
        )

        success_count = 0
        failed_count = 0
        is_stopped = False

        for idx, q in enumerate(quizzes, start=1):
            if not STOP_TASKS.get(user_id, False):
                is_stopped = True
                await status_msg.edit_text(f"🛑 Upload Stopped! Posted: `{success_count}/{len(quizzes)}`")
                break

            img_path = f"q_{idx}_{user_id}.png"

            try:
                # 1. सवाल से इमेज बनाएं
                create_question_image(idx, q["question"], q["options"], img_path)

                # 2. चैनल पर इमेज भेजें
                with open(img_path, "rb") as photo:
                    await client.send_photo(
                        chat_id=target_chat_id,
                        photo=photo,
                        caption=f"❓ **Question #{idx}**"
                    )

                # 3. इमेज के नीचे असली Quiz Poll भेजें
                await client.send_poll(
                    chat_id=target_chat_id,
                    question=f"Choose correct option for Q{idx}:",
                    options=["Option A", "Option B", "Option C", "Option D"],
                    type=PollType.QUIZ,
                    correct_option_id=int(q["correct_option_id"]),
                    explanation=q["explanation"],
                    is_anonymous=True
                )

                success_count += 1
                await asyncio.sleep(2.5)  # Delay to prevent flood wait

            except Exception as e:
                failed_count += 1
                print(f"Error on Q{idx}: {e}")

            finally:
                # लोकल जनरेटेड इमेज फाइल हटाएं
                if os.path.exists(img_path):
                    os.remove(img_path)

        if not is_stopped:
            await status_msg.edit_text(
                f"✅ **Upload Completed!**\n\n"
                f"📊 Uploaded: **{success_count}/{len(quizzes)}**\n"
                f"🎯 Destination: `{target_chat_id}`"
            )

    except Exception as e:
        await status_msg.edit_text(f"❌ **Main Error:** `{e}`")

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
    if user_id in STOP_TASKS and STOP_TASKS[user_id]:
        STOP_TASKS[user_id] = False
        await message.reply_text("🛑 Stop Signal Sent!")
    else:
        await message.reply_text("⚠️ No active upload task.")


# ============================================================
# RUN BOT
# ============================================================

if __name__ == "__main__":
    print("QUIZ BOT STARTING...")
    app.run()