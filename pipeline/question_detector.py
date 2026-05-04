# pipeline/question_detector.py
# LLM agent that reads a raw incoming RFP and extracts all questions with their positions.

import json
from docx import Document
from ingest.cleaner import normalize_text, clean_question
from pipeline.llm_provider import chat_completion

DETECTION_SYSTEM_PROMPT = """You are an expert at reading Request for Proposal (RFP) documents used in investment management.

Your job is to identify every question in the document that requires a written response from the recipient.

Questions may appear as:
- Sentences ending in "?"
- Directive phrases: "Please describe...", "Provide details on...", "Explain...", "Outline..."
- Numbered or lettered items: "1.", "2.1", "A.", "B."
- Table rows where the cell contains a question or directive

Ignore: section headers, instructions to respondents, cover pages, legal boilerplate, table of contents.

Respond ONLY with a JSON array of objects. Each object must have:
  "index": integer (0-based order in document)
  "question": the full question text, cleaned of numbering prefixes

No preamble. No markdown fences. Example:
[
  {"index": 0, "question": "What is your firm's full legal name?"},
  {"index": 1, "question": "Please describe your investment philosophy and process."}
]"""


def _extract_raw_text(docx_path: str) -> str:
    doc = Document(docx_path)
    parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text.strip())
    # Also extract table cells
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text.strip())
    return "\n\n".join(parts)


def detect_questions(docx_path: str) -> list[dict]:
    """
    Returns a list of {index, question} dicts extracted from a raw RFP.
    Questions are normalized and cleaned.
    """
    raw_text = _extract_raw_text(docx_path)
    raw_text = normalize_text(raw_text)

    # Chunk if large
    MAX_CHARS = 12_000
    chunks = [raw_text[i:i+MAX_CHARS] for i in range(0, len(raw_text), MAX_CHARS)]

    all_questions = []
    offset = 0
    for chunk in chunks:
        raw_response = chat_completion(DETECTION_SYSTEM_PROMPT, chunk)
        try:
            questions = json.loads(raw_response)
            for q in questions:
                q["index"] = q.get("index", 0) + offset
                q["question"] = clean_question(q.get("question", ""))
            all_questions.extend(questions)
            offset += len(questions)
        except json.JSONDecodeError:
            print(f"[WARN] question_detector: failed to parse chunk response")

    return all_questions
