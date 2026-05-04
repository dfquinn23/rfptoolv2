# core/logger.py
# Structured JSONL logging for pipeline runs and ingestion events.

import json
import os
from datetime import datetime
from core.config import LOG_DIR


def _write(record: dict, filename: str) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, filename)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def log_match(question: str, answer: str, score: float, source: str, tier: str) -> None:
    _write({
        "event": "match",
        "timestamp": datetime.utcnow().isoformat(),
        "question": question,
        "answer_preview": answer[:120],
        "score": score,
        "source": source,
        "tier": tier,
    }, "pipeline.jsonl")


def log_ingest(source_file: str, pairs_extracted: int, pairs_ingested: int, skipped: int) -> None:
    _write({
        "event": "ingest",
        "timestamp": datetime.utcnow().isoformat(),
        "source_file": source_file,
        "pairs_extracted": pairs_extracted,
        "pairs_ingested": pairs_ingested,
        "skipped": skipped,
    }, "ingest.jsonl")


def log_error(context: str, error: str) -> None:
    _write({
        "event": "error",
        "timestamp": datetime.utcnow().isoformat(),
        "context": context,
        "error": error,
    }, "errors.jsonl")
