from quizbot.runner.handlers import quiz_handler
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

from html2image import Html2Image


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

# Initialize HTML-to-Image renderer with Railway Chromium flags
hti = Html2Image(
    custom_flags=[
        '--no-sandbox',
        '--disable-gpu',
        '--disable-software-rasterizer',
        '--disable-dev-shm-usage'
    ]
)


# ============================================================
# HTML ENGINE (TELEGRAM DARK THEME TABLE CARD)
# ============================================================

def generate_html_quiz_card(q_num: int, question_text: str) -> str:
    # Match List / Sumelit check to generate table layout
    is_table_question = any(kw in question_text.lower() for kw in ["सूची", "सुमेलित", "match", "list-i"])

    body_content = ""

    if is_table_question and ":" in question_text:
        parts = question_text.split(":", 1)
        header_title = parts[0].strip()
        data_part = parts[1].strip() if len(parts) > 1 else ""

        # Format items into table rows
        rows = data_part.split(",")
        table_rows_html = ""
        for r in rows:
            if "|" in r:
                c1, c2 = r.split("|", 1)
            elif "-" in r:
                c1, c2 = r.split("-", 1)
            else:
                c1, c2 = r, ""
            table_rows_html += f"<tr><td>{c1.strip()}</td><td>{c2.strip()}</td></tr>"

        body_content = f"""
        <div class="q-title">{header_title}</div>
        <table class="match-table">
            {table_rows_html}
        </table>
        """
    else:
        formatted_text = question_text.replace("\n", "<br>")
        body_content = f'<div class="q-text">{formatted_text}</div>'

    html_code = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400;600&display=swap');
            body {{
                background-color: #0e1621;
                margin: 0;
                padding: 20px;
                font-family: 'Noto Sans Devanagari', sans-serif;
                color: #e3e5e8;
                width: 720px;
            }}
            .card {{
                background-color: #17212b;
                border: 1px solid #242f3d;
                border-radius: 12px;
                padding: 20px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
            }}
            .header {{
                color: #5288c1;
                font-size: 20px;
                font-weight: 600;
                margin-bottom: 14px;
                border-bottom: 1px solid #242f3d;
                padding-bottom: 8px;
            }}
            .q-text {{
                font-size: 18px;
                line-height: 1.6;
                white-space: pre-wrap;
            }}
            .match-table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 12px;
            }}
            .match-table td {{
                border: 1px solid #242f3d;
                padding: 10px 12px;
                font-size: 16px;
                vertical-align: top;
            }}
            .match-table tr:nth-child(even) {{
                background-color: #1e2c3a;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="header">📌 Question #{q_num}</div>
            {body_content}
        </div>
    </body>
    </html>
    """
    return html_code


def render_card_image(q_num: int, question_text: str) -> BytesIO:
    html_content = generate_html_quiz_card(q_num, question_text)
    out_filename = f"q_{q_num}.png"
    
    # Render HTML to PNG image
    hti.screenshot(html_str=html_content, save_as=out_filename)
    
    img_stream = BytesIO()
    with open(out_filename, "rb") as f:
        img_stream.write(f.read())
    img_stream.seek(0)

    if os.path.exists(out_filename):
        os.remove(out_filename)

    return img_stream


def should_make_image(question_text: str) -> bool:
    if len(question_text) > 140:
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
        f"👋 **Welcome to Quiz Uploader Bot!**\n\n"
        f"🎯 **Target Chat:** `{saved_chat}`\n\n"
        f"`.txt` file send karke `/quiz` dabaayein.",
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

    status_msg = await message.reply_text("📥 Processing File...")
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
                    # HTML Card Render
                    img_stream = render_card_image(idx, full_question)
                    await client.send_photo(
                        chat_id=target_chat_id,
                        photo=img_stream,
                        caption=f"📌 **Question #{idx}**\n\nSahi uttar niche chunein 👇"
                    )
                    poll_question = f"Question #{idx}: Sahi option chunein:"
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

        await status_msg.edit_text(f"✅ Upload Complete! Total Posted: **{success_count}/{len(quizzes)}**")

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
