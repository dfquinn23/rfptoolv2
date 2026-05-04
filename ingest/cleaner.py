# ingest/cleaner.py
# Text normalization before extraction or embedding.
# These are deterministic transforms — no LLM needed here.

import re
import unicodedata


def normalize_text(text: str) -> str:
    """
    Full normalization pipeline:
    1. Unicode → ASCII-safe equivalents (smart quotes, em dashes, etc.)
    2. Remove non-printable control characters
    3. Collapse excessive whitespace
    4. Strip leading/trailing whitespace
    """
    # Unicode normalization: convert smart quotes, em dashes, etc.
    text = unicodedata.normalize("NFKC", text)

    # Common substitutions that NFKC misses
    replacements = {
        "\u2018": "'", "\u2019": "'",   # smart single quotes
        "\u201c": '"', "\u201d": '"',   # smart double quotes
        "\u2013": "-", "\u2014": "-",   # en dash, em dash
        "\u2022": "-",                   # bullet •
        "\u00a0": " ",                   # non-breaking space
        "\u2026": "...",                 # ellipsis …
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)

    # Remove non-printable control characters (except newline/tab)
    text = re.sub(r"[^\S\n\t ]+", " ", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)

    # Collapse runs of spaces/tabs
    text = re.sub(r"[ \t]+", " ", text)

    # Collapse 3+ consecutive newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def is_empty_answer(text: str, empty_patterns: list) -> bool:
    """
    Return True if the answer is semantically empty and should be skipped.
    Checks both pattern matching and word count.
    """
    from core.config import MIN_ANSWER_WORD_COUNT
    cleaned = text.strip().lower()
    if not cleaned:
        return True
    if len(cleaned.split()) < MIN_ANSWER_WORD_COUNT:
        return True
    for pattern in empty_patterns:
        if cleaned.startswith(pattern) and len(cleaned.split()) < 8:
            return True
    return False


def clean_question(text: str) -> str:
    """Normalize a question string."""
    text = normalize_text(text)
    # Remove leading numbering: "1.", "1.2", "A.", "Q1:", etc.
    text = re.sub(r"^[\d]+[\.\)]\s*", "", text)
    text = re.sub(r"^[A-Za-z][\.\)]\s*", "", text)
    text = re.sub(r"^Q\d+[:\.]?\s*", "", text, flags=re.IGNORECASE)
    return text.strip()
