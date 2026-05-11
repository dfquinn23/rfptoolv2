"""
ingest/extractor.py

Extracts Q&A pairs from completed RFP response documents using an LLM.

Supports: .docx, .doc (requires Word on Windows via pywin32)

Approach:
  1. Pull raw text from the document
  2. Send to LLM with a structured extraction prompt
     (chunks large documents to avoid output token limits)
  3. Parse and return clean Q&A pairs as JSON
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from docx import Document
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EXTRACTION_MODEL = "gpt-4o-mini"

# Max characters per chunk sent to LLM.
# gpt-4o-mini input window: 128k tokens (~96k words).
# We keep chunks small enough that the JSON output won't exceed 16k output tokens.
CHUNK_SIZE = 20000  # characters (~5000 words) — safe for large answer sets

SYSTEM_PROMPT = """You are an expert at analyzing completed RFP (Request for Proposal)
response documents. Your job is to extract every question-answer pair from the document.

Rules:
- A "question" is any prompt, request, or inquiry that the responding firm was asked to address.
  This includes items that don't end in "?" — numbered items, bullet points, section prompts,
  "Please describe...", "Provide information on...", etc.
- An "answer" is the response the firm provided to that question.
- IGNORE all boilerplate: cover pages, confidentiality statements, table of contents,
  submission instructions, timelines, contact information blocks, section headers,
  page numbers, and any text that is not part of a Q&A exchange.
- If a question has multiple parts, keep it as one question with one combined answer.
- If an answer spans multiple paragraphs, combine them into a single answer string
  with newlines preserved.
- Do not fabricate, summarize, or rephrase. Extract verbatim.
- If this is a partial document (a section or chunk), only extract pairs visible in this section.

Return a JSON object with a single key "pairs" containing the array of results.
Format: {"pairs": [{"question": "...", "answer": "..."}, ...]}
If no Q&A pairs are found, return: {"pairs": []}"""


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_text_from_docx(path: str) -> str:
    """Extract all paragraph and table text from a .docx file."""
    doc = Document(path)
    lines = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            lines.append(text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if text:
                    lines.append(text)
    return "\n".join(lines)


def _extract_text_from_doc(path: str) -> str:
    """
    Extract text from a legacy .doc file using Word COM automation (Windows only).
    Requires pywin32: pip install pywin32
    """
    try:
        import win32com.client
        import pythoncom
    except ImportError:
        raise ImportError(
            "pywin32 is required to process .doc files on Windows.\n"
            "Run: pip install pywin32\n"
            "Or convert the file to .docx manually in Word."
        )

    pythoncom.CoInitialize()
    word = None
    doc = None
    tmp_path = None

    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        abs_path = str(Path(path).resolve())
        doc = word.Documents.Open(abs_path)
        tmp_path = tempfile.mktemp(suffix=".docx")
        doc.SaveAs2(tmp_path, FileFormat=16)
        doc.Close(False)
        doc = None
        return _extract_text_from_docx(tmp_path)
    finally:
        if doc:
            try:
                doc.Close(False)
            except Exception:
                pass
        if word:
            try:
                word.Quit()
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        pythoncom.CoUninitialize()


def extract_raw_text(file_path: str) -> str:
    """Extract raw text from a .docx or .doc file."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _extract_text_from_docx(str(path))
    elif suffix == ".doc":
        return _extract_text_from_doc(str(path))
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Supported: .docx, .doc")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """
    Split text into chunks of ~chunk_size characters, breaking at newlines
    so Q&A pairs are never split mid-paragraph.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Walk back to the nearest newline to avoid splitting mid-paragraph
        split_at = text.rfind("\n", start, end)
        if split_at == -1 or split_at <= start:
            split_at = end  # no newline found, hard cut
        chunks.append(text[start:split_at])
        start = split_at + 1

    return chunks


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

def _call_llm_single(client: OpenAI, text: str) -> list[dict]:
    """Make a single LLM call and return parsed pairs."""
    response = client.chat.completions.create(
        model=EXTRACTION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract all Q&A pairs from this document:\n\n{text}"},
        ],
        temperature=0,
        max_tokens=16000,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM returned invalid JSON: {e}\nRaw output:\n{raw[:500]}"
        )

    pairs = parsed.get("pairs", [])
    if not isinstance(pairs, list):
        raise ValueError(f"Expected 'pairs' to be a list, got: {type(pairs)}")

    return pairs


def _call_llm(text: str) -> list[dict]:
    """
    Send document text to the LLM. Automatically chunks large documents
    to avoid hitting output token limits.
    """
    os.environ.pop("SSL_CERT_FILE", None)
    client = OpenAI(api_key=OPENAI_API_KEY)

    chunks = _chunk_text(text)
    if len(chunks) > 1:
        print(f"      [INFO] Document split into {len(chunks)} chunks for extraction.")

    all_pairs = []
    for i, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            print(f"      [INFO] Processing chunk {i}/{len(chunks)}...")
        pairs = _call_llm_single(client, chunk)
        all_pairs.extend(pairs)

    return all_pairs


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def extract_qa_pairs(file_path: str) -> list[dict]:
    """
    Extract Q&A pairs from a completed RFP response document.

    Args:
        file_path: Path to a .docx or .doc file.

    Returns:
        List of dicts: [{"question": str, "answer": str, "source": str}, ...]
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not OPENAI_API_KEY:
        raise EnvironmentError("OPENAI_API_KEY is not set.")

    text = extract_raw_text(str(path))

    if not text.strip():
        raise ValueError(f"No text could be extracted from {path.name}")

    pairs = _call_llm(text)

    result = []
    for item in pairs:
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if question and answer:
            result.append({
                "question": question,
                "answer": answer,
                "source": path.name,
            })

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extractor.py <path_to_rfp.docx>")
        sys.exit(1)

    pairs = extract_qa_pairs(sys.argv[1])
    print(f"\n✅ Extracted {len(pairs)} Q&A pairs\n")
    for idx, pair in enumerate(pairs, 1):
        print(f"--- Pair {idx} ---")
        print(f"Q: {pair['question'][:120]}")
        print(f"A: {pair['answer'][:200]}")
        print()

    out_path = "extracted_qa.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pairs, f, indent=2, ensure_ascii=False)
    print(f"💾 Saved to {out_path}")
