# ingest/validator.py
# Filters and deduplicates Q&A pairs before they hit the vector database.

from ingest.cleaner import is_empty_answer
from core.config import EMPTY_ANSWER_PATTERNS, MIN_ANSWER_WORD_COUNT


def validate_pairs(pairs: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Split pairs into (valid, skipped).
    Skipped pairs include a 'skip_reason' key for review.
    """
    valid = []
    skipped = []

    seen_answers = set()

    for pair in pairs:
        q = pair.get("question", "").strip()
        a = pair.get("answer", "").strip()

        # Missing question or answer
        if not q:
            skipped.append({**pair, "skip_reason": "empty_question"})
            continue
        if not a:
            skipped.append({**pair, "skip_reason": "empty_answer"})
            continue

        # Semantically empty answer
        if is_empty_answer(a, EMPTY_ANSWER_PATTERNS):
            skipped.append({**pair, "skip_reason": "low_value_answer"})
            continue

        # Near-duplicate detection (exact answer dedup)
        answer_key = a.lower().strip()
        if answer_key in seen_answers:
            skipped.append({**pair, "skip_reason": "duplicate_answer"})
            continue

        seen_answers.add(answer_key)
        valid.append(pair)

    return valid, skipped


def validation_report(valid: list[dict], skipped: list[dict]) -> str:
    """Return a human-readable summary of validation results."""
    total = len(valid) + len(skipped)
    reasons = {}
    for p in skipped:
        r = p.get("skip_reason", "unknown")
        reasons[r] = reasons.get(r, 0) + 1

    lines = [
        f"Total extracted: {total}",
        f"Valid pairs: {len(valid)}",
        f"Skipped: {len(skipped)}",
    ]
    for reason, count in reasons.items():
        lines.append(f"  • {reason}: {count}")
    return "\n".join(lines)
