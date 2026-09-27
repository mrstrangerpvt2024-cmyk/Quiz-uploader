from quizbot.runner.handlers import quiz_handler  # Path check kar lein
import os
import re
import uuid
import asyncio
import textwrap
from threading import Thread

from flask import Flask

from PIL import Image, ImageDraw, ImageFont

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.enums import PollType


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
# QUIZ CARD IMAGE GENERATOR
# (Screenshot jaisa dark bordered quiz card banata hai)
# ============================================================

# NOTE:
# Devanagari (Hindi) text render karne ke liye "Noto Sans Devanagari"
# font chahiye hota hai — normal system font Hindi text nahi dikhata.
#
# Bot ke folder me ek "fonts" folder banayein aur usme yeh 2 files
# daalein (Google Fonts se free download hoti hain):
#
#   fonts/NotoSansDevanagari-Regular.ttf
#   fonts/NotoSansDevanagari-Bold.ttf
#
# Download link: https://fonts.google.com/noto/specimen/Noto+Sans+Devanagari
#
# Agar font files nahi milti to code automatically default font
# use kar lega (par tab Hindi text theek se render nahi hoga).

FONT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fonts"
)

FONT_BOLD_PATH = os.path.join(
    FONT_DIR,
    "NotoSansDevanagari-Bold.ttf"
)

FONT_REGULAR_PATH = os.path.join(
    FONT_DIR,
    "NotoSansDevanagari-Regular.ttf"
)

QUIZ_CARD_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "quiz_cards"
)

os.makedirs(QUIZ_CARD_DIR, exist_ok=True)


def _load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def create_quiz_card_image(
    q_number: int,
    question: str,
    options: list
) -> str:
    """
    Telegram 'Advance Quiz Bot' jaisa dark bordered quiz card
    image generate karta hai aur file path return karta hai.
    """

    # ---------------- COLORS ----------------
    BG_COLOR = (12, 17, 29)
    HEADER_BG = (26, 33, 53)
    HEADER_BORDER = (60, 72, 105)
    OPTION_BG = (20, 26, 43)
    OPTION_BORDER = (55, 66, 96)
    TEXT_COLOR = (238, 240, 245)
    LABEL_COLOR = (120, 170, 255)

    WIDTH = 1000
    PADDING = 36
    RADIUS = 16

    # ---------------- FONTS ----------------
    font_q = _load_font(FONT_BOLD_PATH, 32)
    font_opt = _load_font(FONT_REGULAR_PATH, 28)

    # ---------------- WRAP TEXT ----------------
    wrapped_question = textwrap.wrap(
        f"Q{q_number}. {question}",
        width=42
    ) or [""]

    labels = ["A", "B", "C", "D"]

    wrapped_options = []
    for label, opt in zip(labels, options):
        wrapped_options.append(
            textwrap.wrap(
                f"{label})  {opt}",
                width=48
            ) or [f"{label})"]
        )

    # ---------------- CALCULATE HEIGHTS ----------------
    q_line_h = 46
    opt_line_h = 40

    question_block_h = len(wrapped_question) * q_line_h + 40

    option_block_heights = [
        len(lines) * opt_line_h + 26
        for lines in wrapped_options
    ]

    total_height = (
        PADDING * 2
        + question_block_h
        + sum(option_block_heights)
        + (len(wrapped_options) - 1) * 16
        + 20
    )

    # ---------------- DRAW IMAGE ----------------
    img = Image.new("RGB", (WIDTH, total_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = PADDING

    # ---- Question card ----
    draw.rounded_rectangle(
        [PADDING - 10, y - 10, WIDTH - PADDING + 10, y + question_block_h],
        radius=RADIUS,
        fill=HEADER_BG,
        outline=HEADER_BORDER,
        width=2
    )

    ty = y + 12
    for line in wrapped_question:
        draw.text(
            (PADDING + 12, ty),
            line,
            font=font_q,
            fill=TEXT_COLOR
        )
        ty += q_line_h

    y += question_block_h + 22

    # ---- Option cards ----
    for lines, h in zip(wrapped_options, option_block_heights):

        draw.rounded_rectangle(
            [PADDING - 10, y, WIDTH - PADDING + 10, y + h],
            radius=12,
            fill=OPTION_BG,
            outline=OPTION_BORDER,
            width=2
        )

        ty = y + 13
        for i, line in enumerate(lines):
            draw.text(
                (PADDING + 16, ty),
                line,
                font=font_opt,
                fill=LABEL_COLOR if i == 0 else TEXT_COLOR
            )
            ty += opt_line_h

        y += h + 16

    # ---------------- SAVE ----------------
    filename = os.path.join(
        QUIZ_CARD_DIR,
        f"quiz_{uuid.uuid4().hex}.png"
    )

    img.save(filename)

    return filename


# ============================================================
# QUIZ PARSER
# ============================================================

def parse_quiz_file(file_content: str) -> list:

    quizzes = []

    # A = 0
    # B = 1
    # C = 2
    # D = 3

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

        # Empty line skip
        if not line:
            continue

        # Pipe hona zaroori hai
        if "|" not in line:
            continue

        # EXACTLY maximum 4 parts:
        #
        # Question
        # Options
        # Answer
        # Explanation
        #
        parts = [
            x.strip()
            for x in line.split("|", 3)
        ]

        if len(parts) < 3:

            print(
                f"[SKIP Q{line_no}] "
                f"Invalid pipe format"
            )

            continue


        # ====================================================
        # QUESTION
        # ====================================================

        question = parts[0].strip()

        # Q1.
        # Q2.
        # Q10.
        # Q 1.
        #
        # remove

        question = re.sub(
            r"^Q\s*\d+\.\s*",
            "",
            question,
            flags=re.IGNORECASE
        ).strip()


        # ====================================================
        # OPTIONS
        # ====================================================

        option_text = parts[1].strip()

        #
        # Expected:
        #
        # A) अनुच्छेद 12-18,
        # B) अनुच्छेद 14-18,
        # C) अनुच्छेद 19-22,
        # D) अनुच्छेद 23-24
        #

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


        # Exactly 4 options required

        if len(options) != 4:

            print(
                f"[SKIP Q{line_no}] "
                f"Expected 4 options, "
                f"found {len(options)}"
            )

            print(
                f"RAW OPTIONS: {option_text}"
            )

            continue


        # ====================================================
        # CORRECT ANSWER
        # ====================================================

        raw_answer = parts[2].strip().upper()

        # Answer field se A/B/C/D nikaalo

        answer_match = re.search(
            r"[A-D]",
            raw_answer
        )

        if not answer_match:

            print(
                f"[SKIP Q{line_no}] "
                f"Invalid answer: {raw_answer}"
            )

            continue


        correct_letter = answer_match.group(0)

        # A=0
        # B=1
        # C=2
        # D=3

        correct_option_id = answer_map[
            correct_letter
        ]


        # ====================================================
        # EXPLANATION
        # ====================================================

        explanation = ""

        if len(parts) >= 4:
            explanation = parts[3].strip()

        # Telegram explanation limit
        explanation = explanation[:200]


        # ====================================================
        # SAVE QUIZ
        # ====================================================

        quiz_data = {
            "question": question,
            "options": options,
            "correct_option_id": correct_option_id,
            "correct_answer": correct_letter,
            "explanation": explanation
        }

        quizzes.append(
            quiz_data
        )


        # ====================================================
        # DEBUG LOG
        # ====================================================

        print(
            "======================================"
        )

        print(
            f"QUESTION {line_no}"
        )

        print(
            f"ANSWER LETTER: {correct_letter}"
        )

        print(
            f"CORRECT OPTION ID: {correct_option_id}"
        )

        print(
            f"OPTIONS: {options}"
        )

        print(
            "======================================"
        )


    return quizzes


# ============================================================
# /START
# ============================================================

@app.on_message(
    filters.command("start")
)
async def start_cmd(
    client: Client,
    message: Message
):

    user_id = message.from_user.id

    saved_chat = USER_CHAT_CONFIG.get(
        user_id,
        "Not Set"
    )


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

        f"🎯 **Saved Target Chat ID:** "
        f"`{saved_chat}`\n\n"

        f"📌 **Instructions:**\n"

        f"1. **Chat ID Set Karein:** "
        f"Button par click karein aur Chat ID bhejein.\n\n"

        f"2. `.txt` Quiz File bhejein.\n\n"

        f"3. Format:\n"
        f"`Question | A) Opt1, B) Opt2, C) Opt3, D) Opt4 | B | Explanation`\n\n"

        f"4. `/quiz` se upload start karein.\n\n"

        f"5. `/stop` se upload rok sakte hain.",

        reply_markup=keyboard
    )


# ============================================================
# SAVE CHAT ID BUTTON
# ============================================================

@app.on_callback_query(
    filters.regex("^btn_save_chat_id$")
)
async def cb_save_chat(
    client: Client,
    callback_query: CallbackQuery
):

    user_id = callback_query.from_user.id

    AWAITING_CHAT_ID.add(
        user_id
    )


    await callback_query.message.reply_text(

        "✏️ **Direct Target Chat ID Bhejein:**\n\n"

        "Ab bina command ke sirf Chat ID bhejein.\n\n"

        "Example:\n"
        "`-1004399820534`"
    )


    await callback_query.answer()


# ============================================================
# DIRECT CHAT ID INPUT
# ============================================================

@app.on_message(
    filters.text &
    ~filters.command([
        "start",
        "quiz",
        "stop"
    ])
)
async def handle_direct_chat_id_input(
    client: Client,
    message: Message
):

    user_id = message.from_user.id

    if user_id not in AWAITING_CHAT_ID:
        return


    raw_input = message.text.strip()


    try:

        chat_id_int = int(
            raw_input
        )

        USER_CHAT_CONFIG[
            user_id
        ] = chat_id_int

        AWAITING_CHAT_ID.remove(
            user_id
        )


        await message.reply_text(

            f"✅ **Chat ID Successfully Saved!**\n\n"

            f"🎯 **Target Chat ID:** "
            f"`{chat_id_int}`\n\n"

            f"Ab `.txt` quiz file bhej sakte hain."
        )


    except ValueError:

        await message.reply_text(

            "❌ **Invalid Chat ID!**\n\n"

            "Sirf numeric Chat ID bhejein.\n\n"

            "Example:\n"
            "`-1004399820534`"
        )


# ============================================================
# TXT FILE UPLOAD
# ============================================================

@app.on_message(
    filters.document
)
async def handle_document_upload(
    client: Client,
    message: Message
):

    user_id = message.from_user.id

    file_name = (
        message.document.file_name
        or ""
    )


    if not file_name.lower().endswith(
        ".txt"
    ):

        await message.reply_text(
            "❌ Sirf `.txt` quiz files allowed hain."
        )

        return


    USER_ACTIVE_FILES[
        user_id
    ] = message


    target_chat = USER_CHAT_CONFIG.get(
        user_id
    )


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

            "⚠️ **Target Chat ID Set Nahi Hai!**\n\n"

            "Pehle Save Chat ID button se "
            "target channel/group ki Chat ID save karein.",

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

        f"📄 **File Received:** "
        f"`{file_name}`\n\n"

        f"🎯 **Target Chat:** "
        f"`{target_chat}`\n\n"

        f"🚀 `/quiz` type karein "
        f"ya button dabayein.",

        reply_markup=keyboard
    )


# ============================================================
# QUIZ UPLOAD
# ============================================================

@app.on_message(
    filters.command("quiz")
)
@app.on_callback_query(
    filters.regex("^btn_trigger_quiz$")
)
async def start_quiz_process(
    client: Client,
    union_obj
):

    # Callback button
    if isinstance(
        union_obj,
        CallbackQuery
    ):

        message = union_obj.message

        user_id = (
            union_obj.from_user.id
        )

        await union_obj.answer()

    # /quiz command
    else:

        message = union_obj

        user_id = (
            union_obj.from_user.id
        )


    target_chat = USER_CHAT_CONFIG.get(
        user_id
    )

    doc_message = USER_ACTIVE_FILES.get(
        user_id
    )


    # ========================================================
    # CHECK CHAT ID
    # ========================================================

    if not target_chat:

        await message.reply_text(
            "❌ Target Chat ID set nahi hai!"
        )

        return


    # ========================================================
    # CHECK FILE
    # ========================================================

    if not doc_message:

        await message.reply_text(
            "❌ Koi `.txt` quiz file nahi mili!"
        )

        return


    status_msg = await message.reply_text(
        "📥 Downloading & Processing File..."
    )


    file_path = None


    try:

        # ====================================================
        # DOWNLOAD FILE
        # ====================================================

        file_path = await doc_message.download()


        # ====================================================
        # READ FILE
        # ====================================================

        with open(
            file_path,
            "r",
            encoding="utf-8-sig"
        ) as f:

            content = f.read()


        # ====================================================
        # PARSE
        # ====================================================

        quizzes = parse_quiz_file(
            content
        )


        if not quizzes:

            await status_msg.edit_text(

                "❌ **No Quiz Found!**\n\n"

                "File format check karein:\n\n"

                "`Question | A) Option 1, B) Option 2, C) Option 3, D) Option 4 | B | Explanation`"
            )

            return


        # ====================================================
        # CHAT ACCESS
        # ====================================================

        try:

            target_chat_id = int(
                target_chat
            )

            await client.get_chat(
                target_chat_id
            )

        except Exception as e:

            await status_msg.edit_text(

                f"❌ **Target Chat Access Error**\n\n"
                f"`{e}`\n\n"

                f"Bot ko target channel/group me "
                f"admin/member permission dein."
            )

            return


        # ====================================================
        # START
        # ====================================================

        await status_msg.edit_text(

            f"🚀 **Quiz Upload Started!**\n\n"

            f"📊 Total Questions: "
            f"`{len(quizzes)}`\n"

            f"🎯 Target: "
            f"`{target_chat_id}`\n\n"

            f"🛑 Stop karne ke liye `/stop` bhejein."
        )


        STOP_TASKS[
            user_id
        ] = True


        success_count = 0
        failed_count = 0
        is_stopped = False


        # ====================================================
        # UPLOAD LOOP
        # ====================================================

        for idx, q in enumerate(
            quizzes,
            start=1
        ):


            # -----------------------------------------------
            # STOP CHECK
            # -----------------------------------------------

            if not STOP_TASKS.get(
                user_id,
                False
            ):

                is_stopped = True

                await status_msg.edit_text(

                    f"🛑 **Upload Stopped!**\n\n"

                    f"📊 Posted: "
                    f"`{success_count}/{len(quizzes)}`"
                )

                break


            try:

                # -------------------------------------------
                # FINAL SAFETY CHECK
                # -------------------------------------------

                correct_id = int(
                    q["correct_option_id"]
                )


                # -------------------------------------------
                # DEBUG
                # -------------------------------------------

                print(
                    "======================================"
                )

                print(
                    f"SENDING Q{idx}"
                )

                print(
                    f"ANSWER LETTER: "
                    f"{q['correct_answer']}"
                )

                print(
                    f"CORRECT OPTION ID: "
                    f"{correct_id}"
                )

                print(
                    f"OPTIONS: "
                    f"{q['options']}"
                )

                print(
                    "POLL TYPE: QUIZ"
                )

                print(
                    "======================================"
                )


                # -------------------------------------------
                # SEND STYLED QUIZ CARD IMAGE
                # (bordered image, screenshot jaisa)
                # -------------------------------------------

                card_path = None

                try:

                    card_path = create_quiz_card_image(
                        idx,
                        q["question"],
                        q["options"]
                    )

                    await client.send_photo(
                        chat_id=target_chat_id,
                        photo=card_path
                    )

                    await asyncio.sleep(1)

                except Exception as img_err:

                    print(
                        f"IMAGE CARD ERROR Q{idx}: {repr(img_err)}"
                    )

                finally:

                    if (
                        card_path
                        and
                        os.path.exists(card_path)
                    ):

                        try:
                            os.remove(card_path)
                        except Exception:
                            pass


                # -------------------------------------------
                # SEND REAL QUIZ POLL
                # -------------------------------------------

                await client.send_poll(

                    chat_id=target_chat_id,

                    question=(
                        f"{idx}. "
                        f"{q['question']}"
                    ),

                    options=q["options"],

                    # VERY IMPORTANT
                    # This creates Telegram QUIZ,
                    # not normal poll.

                    type=PollType.QUIZ,

                    # A = 0
                    # B = 1
                    # C = 2
                    # D = 3

                    correct_option_id=correct_id,

                    # Explanation shown after
                    # answering the quiz.

                    explanation=q[
                        "explanation"
                    ],

                    is_anonymous=True
                )


                success_count += 1


                # Small delay
                await asyncio.sleep(
                    2.5
                )


            except Exception as e:

                failed_count += 1

                print(
                    f"ERROR Q{idx}: {repr(e)}"
                )


                await status_msg.edit_text(

                    f"❌ **Upload Failed!**\n\n"

                    f"Question: `{idx}`\n"

                    f"Error:\n"
                    f"`{e}`"
                )


                is_stopped = True

                break


        # ====================================================
        # COMPLETED
        # ====================================================

        if not is_stopped:

            await status_msg.edit_text(

                f"✅ **Upload Completed!**\n\n"

                f"📊 Uploaded: "
                f"**{success_count}/{len(quizzes)}**\n"

                f"⚠️ Failed: "
                f"**{failed_count}**\n\n"

                f"🎯 Destination:\n"
                f"`{target_chat_id}`"
            )


    except Exception as e:

        print(
            f"MAIN ERROR: {repr(e)}"
        )

        try:

            await status_msg.edit_text(

                f"❌ **Error:**\n\n"
                f"`{e}`"
            )

        except Exception:
            pass


    finally:

        # Remove stop state

        STOP_TASKS.pop(
            user_id,
            None
        )


        # Remove downloaded file

        if (
            file_path
            and
            os.path.exists(file_path)
        ):

            try:
                os.remove(file_path)
            except Exception:
                pass


# ============================================================
# /STOP
# ============================================================

@app.on_message(
    filters.command("stop")
)
async def stop_quiz_process(
    client: Client,
    message: Message
):

    user_id = message.from_user.id


    if (
        user_id in STOP_TASKS
        and
        STOP_TASKS[user_id]
    ):

        STOP_TASKS[
            user_id
        ] = False


        await message.reply_text(

            "🛑 **Stop Signal Sent!**\n\n"
            "Current upload process stop ho raha hai..."
        )


    else:

        await message.reply_text(

            "⚠️ Koi active quiz upload process nahi hai."
        )


# ============================================================
# RUN BOT
# ============================================================

if __name__ == "__main__":

    print(
        "======================================"
    )

    print(
        "QUIZ BOT STARTING..."
    )

    print(
        "======================================"
    )

    app.run()
