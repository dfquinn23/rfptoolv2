"""
pipeline/question_detector.py

Extracts questions from an incoming (unanswered) RFP document.

Unlike ingest/extractor.py — which handles completed RFPs and extracts Q&A
pairs — this module only extracts questions.  The document has no answers yet;
that's what the pipeline is here to provide.

Handles all common incoming RFP formats:
  - Numbered questions  (1.1, 2.3.1, Section A #4)
  - Bullet / dash lists (- Please describe..., • Provide an overview of...)
  - Bold question text  (paragraph where all runs are bold)
  - Plain prose         (paragraph ending in "?")
  - Tables              (question in left/header cell, answer column blank)
  - Mix of the above

The LLM reads the raw text and returns a JSON array of question strings,
preserving document order.  Large documents are automatically chunked so
no call exceeds the model's output token limit.

Output:
  A list[str] of question texts, ready to feed into pipeline/router.route_rfp().
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from docx import Document
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OPENAI_MODEL    = "gpt-4o-mini"
MAX_CHUNK_CHARS = 20_000   # split documents larger than this


# ---------------------------------------------------------------------------
# Text extraction (paragraphs + tables)
# ---------------------------------------------------------------------------

def _extract_raw_text(file_path: str) -> str:
    """
    Pull all text from a .docx file — paragraphs and table cells combined —
    preserving document order as closely as python-docx allows.
    """
    doc  = Document(file_path)
    lines: list[str] = []

    # python-docx exposes paragraphs and tables via doc.element.body children,
    # but iterating doc.paragraphs skips table content.  We walk the body
    # ourselves to preserve ordering.
    for block in doc.element.body:
        tag = block.tag.split("}")[-1]  # strip namespace

        if tag == "p":
            # Regular paragraph
            text = "".join(r.text or "" for r in block.iter()
                           if r.tag.split("}")[-1] == "t")
            text = text.strip()
            if text:
                lines.append(text)

        elif tag == "tbl":
            # Table — flatten all cell text with a separator so the LLM can
            # see the structure without needing to parse XML.
            for row in block.iter():
                if row.tag.split("}")[-1] == "tr":
                    cells = []
                    for cell in row.iter():
                        if cell.tag.split("}")[-1] == "tc":
                            cell_text = "".join(
                                r.text or "" for r in cell.iter()
                                if r.tag.split("}")[-1] == "t"
                            ).strip()
                            if cell_text:
                                cells.append(cell_text)
                    if cells:
                        lines.append(" | ".join(cells))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a document analyst specialising in RFP (Request for Proposal) documents.

Your task: read the text below and return ONLY the questions that a respondent
must answer.  Ignore instructions, deadlines, cover-page text, table headers,
field labels, legal disclaimers, and any other non-question content.

A "question" is any item that explicitly asks the respondent to provide
information — regardless of whether it ends with "?" or uses imperative phrasing
("Please describe...", "Provide an overview of...", "List all...").

Return your answer as a valid JSON array of strings, one string per question,
in the order they appear in the document.  Return NOTHING else — no preamble,
no markdown fences, no explanation.

Example output:
["Question one text", "Question two text", "Question three text"]
"""


def _call_llm(client: OpenAI, text_chunk: str) -> list[str]:
    """Send one chunk to the LLM and parse the returned JSON array."""
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": text_chunk},
        ],
    )

    raw = response.choices[0].message.content.strip()

    # Strip accidental markdown fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$",        "", raw)

    try:
        questions = json.loads(raw)
        if isinstance(questions, list):
            return [str(q).strip() for q in questions if str(q).strip()]
    except json.JSONDecodeError:
        print(f"[question_detector] WARNING: LLM returned non-JSON:\n{raw[:200]}")

    return []


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """
    Split text into chunks of at most max_chars, breaking on newlines so
    we never split a question mid-sentence.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 for the newline
        if current_len + line_len > max_chars and current:
            chunks.append("\n".join(current))
            current     = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_questions(file_path: str, verbose: bool = False) -> list[str]:
    """
    Extract all questions from an incoming RFP document.

    Args:
        file_path: Path to a .docx file.
        verbose:   Print chunk-level progress.

    Returns:
        List of question strings in document order.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"[question_detector] File not found: {file_path}")
    if path.suffix.lower() not in {".docx", ".doc"}:
        raise ValueError(f"[question_detector] Expected .docx file, got: {path.suffix}")

    print(f"[question_detector] Reading: {path.name}")
    raw_text = _extract_raw_text(str(path))

    if not raw_text.strip():
        print("[question_detector] WARNING: No text extracted from document.")
        return []

    print(f"[question_detector] Extracted {len(raw_text):,} characters.")

    chunks     = _chunk_text(raw_text)
    client     = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    all_questions: list[str] = []

    for i, chunk in enumerate(chunks, 1):
        if verbose or len(chunks) > 1:
            print(f"[question_detector] Processing chunk {i}/{len(chunks)} "
                  f"({len(chunk):,} chars)...")
        questions = _call_llm(client, chunk)
        all_questions.extend(questions)
        if verbose:
            print(f"  → {len(questions)} question(s) found in this chunk.")

    # Deduplicate while preserving order (chunking can occasionally produce
    # the same question at a boundary)
    seen: set[str] = set()
    unique: list[str] = []
    for q in all_questions:
        key = q.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(q)

    print(f"[question_detector] Done. {len(unique)} question(s) detected.")
    return unique


# ---------------------------------------------------------------------------
# CLI — smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.question_detector <path/to/rfp.docx>")
        print("       python -m pipeline.question_detector <path/to/rfp.docx> --verbose")
        sys.exit(1)

    target  = sys.argv[1]
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    questions = detect_questions(target, verbose=verbose)

    print(f"\n--- Detected Questions ({len(questions)}) ---")
    for i, q in enumerate(questions, 1):
        print(f"  {i:>3}. {q}")
