# ingest/extractor.py
# LLM-based Q&A extraction from historical RFP .docx files.
# Handles tables, numbered lists, multi-part questions, and preamble.

import json
from docx import Document
from openai import OpenAI
from core.config import OPENAI_API_KEY, OPENAI_LLM_MODEL
from ingest.cleaner import normalize_text, clean_question

client = OpenAI(api_key=OPENAI_API_KEY)

EXTRACTION_SYSTEM_PROMPT = """You are an expert at extracting question-answer pairs from completed Request for Proposal (RFP) documents used in investment management.

Your job:
- Read the document text provided and identify ALL question-answer pairs.
- Questions may be formatted as: sentences ending in "?", numbered items ("1.", "2.1"), lettered items ("A.", "B."), directive phrases ("Please describe...", "Provide details on..."), or table rows where the left column is a question.
- Answers follow each question, either in the next paragraph, the next bullet, or the right column of a table.
- IGNORE: cover pages, instructions sections, legal disclaimers, section headers, and any text that is clearly not a Q&A pair.
- If an answer is just "N/A", "See above", "TBD", or otherwise empty, still include it — the caller will filter it.

Respond ONLY with a JSON array. No preamble, no markdown fences. Example format:
[
  {"question": "What is your firm's full legal name?", "answer": "Acme Capital Management, LLC."},
  {"question": "Please describe your investment philosophy.", "answer": "We employ a fundamental, bottom-up approach..."}
]"""


def _extract_docx_text(path: str) -> str:
    """Extract raw text from a .docx file, including table cells."""
    doc = Document(path)
    parts = []

    for element in doc.element.body:
        tag = element.tag.split("}")[-1]

        if tag == "p":
            # Regular paragraph
            text = "".join(run.text for run in element.iterchildren()
                           if run.tag.split("}")[-1] == "r"
                           for t in run.iterchildren()
                           if t.tag.split("}")[-1] == "t")
            if text.strip():
                parts.append(text.strip())

        elif tag == "tbl":
            # Table — extract row by row, joining cells with " | "
            for row in element.iterchildren():
                if row.tag.split("}")[-1] != "tr":
                    continue
                cells = []
                for cell in row.iterchildren():
                    if cell.tag.split("}")[-1] != "tc":
                        continue
                    cell_text = " ".join(
                        p.text_content() if hasattr(p, "text_content") else
                        "".join(t.text or "" for t in p.iter())
                        for p in cell.iterchildren()
                        if p.tag.split("}")[-1] == "p"
                    ).strip()
                    if cell_text:
                        cells.append(cell_text)
                if cells:
                    parts.append(" | ".join(cells))

    return "\n\n".join(parts)


def _call_llm_extractor(raw_text: str) -> list[dict]:
    """
    Send raw document text to the LLM for Q&A extraction.
    Returns a list of {question, answer} dicts.
    Chunks long documents to stay within context limits.
    """
    MAX_CHARS_PER_CHUNK = 12_000
    chunks = [raw_text[i:i+MAX_CHARS_PER_CHUNK]
              for i in range(0, len(raw_text), MAX_CHARS_PER_CHUNK)]

    all_pairs = []
    for chunk in chunks:
        response = client.chat.completions.create(
            model=OPENAI_LLM_MODEL,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": chunk},
            ],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        try:
            pairs = json.loads(raw)
            all_pairs.extend(pairs)
        except json.JSONDecodeError:
            # Log and skip bad chunks rather than crashing
            print(f"[WARN] extractor: failed to parse LLM response for chunk")

    return all_pairs


def extract_qa_pairs(docx_path: str) -> list[dict]:
    """
    Main entry point.
    Returns a list of cleaned {question, answer, source} dicts.
    """
    import os
    raw_text = _extract_docx_text(docx_path)
    raw_text = normalize_text(raw_text)
    pairs = _call_llm_extractor(raw_text)

    cleaned = []
    for p in pairs:
        q = clean_question(p.get("question", "").strip())
        a = normalize_text(p.get("answer", "").strip())
        if q and a:
            cleaned.append({
                "question": q,
                "answer": a,
                "source": os.path.basename(docx_path),
            })

    return cleaned
