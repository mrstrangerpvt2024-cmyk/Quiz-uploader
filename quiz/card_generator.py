import os
import textwrap
from PIL import Image, ImageDraw, ImageFont

def generate_table_card(question_title: str, col1_title: str, col2_title: str, table_rows: list, output_path: str = "quiz_card.png") -> str:
    """
    Generate dark theme table card image with grid borders.
    """
    BG_COLOR = (24, 34, 45)       # Outer Dark BG
    CARD_BG = (32, 44, 58)        # Table Header BG
    BORDER_COLOR = (70, 90, 115)   # Grid Border Color
    TEXT_COLOR = (245, 245, 245)   # Main White Text
    HEADER_COLOR = (130, 200, 255)# Light Blue Header

    WIDTH = 750
    PADDING = 25

    try:
        font_q = ImageFont.truetype("arial.ttf", 22)
        font_head = ImageFont.truetype("arial.ttf", 20)
        font_body = ImageFont.truetype("arial.ttf", 18)
    except:
        font_q = font_head = font_body = ImageFont.load_default()

    # Create dummy image to calculate height
    dummy_img = Image.new("RGB", (WIDTH, 3000), BG_COLOR)
    draw = ImageDraw.Draw(dummy_img)

    y = PADDING

    # 1. Main Question Title Wrap
    q_words = question_title.split(" ")
    q_lines, current_line = [], []
    for w in q_words:
        current_line.append(w)
        bbox = draw.textbbox((0, 0), " ".join(current_line), font=font_q)
        if bbox[2] - bbox[0] > (WIDTH - (PADDING * 2)):
            current_line.pop()
            q_lines.append(" ".join(current_line))
            current_line = [w]
    q_lines.append(" ".join(current_line))

    for line in q_lines:
        draw.text((PADDING, y), line, font=font_q, fill=TEXT_COLOR)
        bbox = draw.textbbox((0, 0), line, font=font_q)
        y += (bbox[3] - bbox[1]) + 8
    
    y += 15  # Space before table

    # 2. Table Drawing
    col_width = (WIDTH - (PADDING * 2)) // 2

    # Draw Header Row
    header_y = y
    draw.rectangle([PADDING, header_y, WIDTH - PADDING, header_y + 45], fill=CARD_BG, outline=BORDER_COLOR, width=2)
    draw.text((PADDING + 12, header_y + 10), col1_title, font=font_head, fill=HEADER_COLOR)
    draw.text((PADDING + col_width + 12, header_y + 10), col2_title, font=font_head, fill=HEADER_COLOR)
    
    # Vertical line in header
    draw.line([(PADDING + col_width, header_y), (PADDING + col_width, header_y + 45)], fill=BORDER_COLOR, width=2)

    y += 45

    # Draw Rows
    for row in table_rows:
        left_text = row[0] if len(row) > 0 else ""
        right_text = row[1] if len(row) > 1 else ""

        # Function to wrap cell text
        def wrap_cell(text, width):
            words = text.split(" ")
            lines, curr = [], []
            for w in words:
                curr.append(w)
                b = draw.textbbox((0, 0), " ".join(curr), font=font_body)
                if b[2] - b[0] > width:
                    curr.pop()
                    lines.append(" ".join(curr))
                    curr = [w]
            lines.append(" ".join(curr))
            return lines

        left_lines = wrap_cell(left_text, col_width - 24)
        right_lines = wrap_cell(right_text, col_width - 24)

        # Calculate row height
        left_h = len(left_lines) * 26
        right_h = len(right_lines) * 26
        row_h = max(left_h, right_h) + 20

        # Draw Row Border
        draw.rectangle([PADDING, y, WIDTH - PADDING, y + row_h], fill=None, outline=BORDER_COLOR, width=2)
        # Vertical Separator
        draw.line([(PADDING + col_width, y), (PADDING + col_width, y + row_h)], fill=BORDER_COLOR, width=2)

        # Write Left Column Lines
        cy = y + 10
        for l in left_lines:
            draw.text((PADDING + 12, cy), l, font=font_body, fill=TEXT_COLOR)
            cy += 24

        # Write Right Column Lines
        cy = y + 10
        for l in right_lines:
            draw.text((PADDING + col_width + 12, cy), l, font=font_body, fill=TEXT_COLOR)
            cy += 24

        y += row_h

    total_height = y + PADDING

    # Crop and Save
    final_card = dummy_img.crop((0, 0, WIDTH, total_height))
    final_card.save(output_path)
    return output_path