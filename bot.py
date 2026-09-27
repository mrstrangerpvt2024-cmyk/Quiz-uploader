import os
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

# Apne quiz folder se functions import karein
from quiz import generate_table_card, parse_match_quiz


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
        f"👋 **Welcome to Matching Quiz Card Bot!**\n\n"
        f"🎯 **Saved Target Chat ID:** `{saved_chat}`\n\n"
        f"📌 **Instructions:**\n"
        f"1. Target Chat ID save karein.\n"
        f"2. Matching Quiz wali `.txt` File bhejein.\n"
        f"3. Bot automatic **Table Border Card Image** banakar upload karega!\n\n"
        f"📝 **File Format:**\n"
        f"`Question Title | List I # List II | Left1 :: Right1 ; Left2 :: Right2 | A) Opt1, B) Opt2, C) Opt3, D) Opt4 | Ans | Explanation`",
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
# QUIZ UPLOAD WITH MATCHING CARD
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
        await message.reply_text("❌ File ya Target Chat ID missing hai!")
        return

    status_msg = await message.reply_text("📥 File Process ho rahi hai...")
    file_path = None

    try:
        file_path = await doc_message.download()

        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        # Naye quiz module parser se parse karein
        quizzes = parse_match_quiz(content)
        if not quizzes:
            await status_msg.edit_text("❌ No Valid Matching Quizzes Found! Check TXT format.")
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

            img_path = f"q_card_{idx}_{user_id}.png"

            try:
                # 1. Image Card Generate Karein
                generate_table_card(
                    question_title=f"{idx}. {q['title']}",
                    col1_title=q["col1_title"],
                    col2_title=q["col2_title"],
                    table_rows=q["rows"],
                    output_path=img_path
                )

                # 2. Telegram par Table Image Card bhejein
                with open(img_path, "rb") as photo:
                    await client.send_photo(
                        chat_id=target_chat_id,
                        photo=photo,
                        caption=f"<b>Match Question #{idx}</b>"
                    )

                # 3. Niche Quiz Poll Bhejein
                await client.send_poll(
                    chat_id=target_chat_id,
                    question=f"Choose correct matching option for Q{idx}:",
                    options=q["options"],
                    type=PollType.QUIZ,
                    correct_option_id=int(q["correct_option_id"]),
                    explanation=q["explanation"],
                    is_anonymous=True
                )

                success_count += 1
                await asyncio.sleep(2.5)  # FloodWait se bachne ke liye delay

            except Exception as e:
                failed_count += 1
                print(f"Error Q{idx}: {e}")

            finally:
                if os.path.exists(img_path):
                    os.remove(img_path)

        if not is_stopped:
            await status_msg.edit_text(
                f"✅ **Upload Completed!**\n\n"
                f"📊 Uploaded: **{success_count}/{len(quizzes)}**\n"
                f"🎯 Destination: `{target_chat_id}`"
            )

    except Exception as e:
        await status_msg.edit_text(f"❌ **Error:** `{e}`")

    finally:
        STOP_TASKS.pop(user_id, None)
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


# ============================================================
# /STOP COMMAND
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
    print("MATCHING QUIZ BOT STARTING...")
    app.run()