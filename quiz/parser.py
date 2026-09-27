import re

def parse_match_quiz(file_content: str) -> list:
    """
    Parses TXT format into Quiz Data structure.
    Format:
    Question Title | Col1_Title # Col2_Title | Left1 :: Right1 ; Left2 :: Right2 | A) Opt1, B) Opt2, C) Opt3, D) Opt4 | Ans | Explanation
    """
    quizzes = []
    answer_map = {"A": 0, "B": 1, "C": 2, "D": 3}

    for line in file_content.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue

        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 5:
            continue

        q_title = re.sub(r"^Q\s*\d+\.\s*", "", parts[0], flags=re.IGNORECASE).strip()

        # Headers
        headers = parts[1].split("#") if "#" in parts[1] else ["List I", "List II"]
        col1_title = headers[0].strip()
        col2_title = headers[1].strip() if len(headers) > 1 else ""

        # Table rows data (Left :: Right ; Left :: Right)
        raw_rows = parts[2].split(";")
        table_rows = []
        for r in raw_rows:
            if "::" in r:
                pair = r.split("::")
                table_rows.append((pair[0].strip(), pair[1].strip()))

        # Options
        opt_matches = re.findall(r"(?:^|,\s*)([A-D])\)\s*(.*?)(?=,\s*[A-D]\)\s*|$)", parts[3], flags=re.IGNORECASE)
        options = [opt.strip() for _, opt in opt_matches if opt.strip()]

        # Answer
        ans_match = re.search(r"[A-D]", parts[4].upper())
        correct_id = answer_map[ans_match.group(0)] if ans_match else 0

        # Explanation
        explanation = parts[5].strip() if len(parts) >= 6 else ""

        quizzes.append({
            "title": q_title,
            "col1_title": col1_title,
            "col2_title": col2_title,
            "rows": table_rows,
            "options": options,
            "correct_option_id": correct_id,
            "explanation": explanation[:200]
        })

    return quizzes