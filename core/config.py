# core/config.py
# Single source of truth for all configuration.
# Never initialize clients here — use core/qdrant_client.py instead.

import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _get_secret(key: str, default: str = "") -> str:
    """Retrieve a secret from Streamlit secrets or environment variables."""
    try:
        return st.secrets.get(key, os.getenv(key, default))
    except Exception:
        return os.getenv(key, default)


# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = _get_secret("OPENAI_API_KEY")
OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
OPENAI_EMBEDDING_DIMENSIONS: int = 1536
OPENAI_LLM_MODEL: str = "gpt-4o"

# ── Qdrant ─────────────────────────────────────────────────────────────────────
QDRANT_URL: str = _get_secret("QDRANT_URL")
QDRANT_API_KEY: str = _get_secret("QDRANT_API_KEY")

# Collection names — constants prevent typo-generated phantom collections
COLLECTION_RFP_ANSWERS: str = "rfp_answers"
COLLECTION_COMPANY_DOCS: str = "company_docs"

# ── Confidence Thresholds ──────────────────────────────────────────────────────
SCORE_AUTO_INSERT: float = 0.85      # ✅ Verified — auto-insert
SCORE_NEEDS_REVIEW: float = 0.65     # ⚠️  Needs Review — insert with flag
# Below SCORE_NEEDS_REVIEW → escalate to company docs RAG or human queue

# ── Ingestion Settings ─────────────────────────────────────────────────────────
MIN_ANSWER_WORD_COUNT: int = 15
EMPTY_ANSWER_PATTERNS: list = [
    "n/a", "not applicable", "see above", "see attached", "refer to",
    "please see", "same as above", "as noted above", "tbd", "to be determined",
]
DOC_CHUNK_SIZE: int = 400
DOC_CHUNK_OVERLAP: int = 50
RETRIEVAL_TOP_K: int = 5

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR: str = "data"
PAST_RFPS_DIR: str = os.path.join(DATA_DIR, "past_rfps")
COMPANY_DOCS_DIR: str = os.path.join(DATA_DIR, "company_docs")
OUTPUT_DIR: str = os.path.join(DATA_DIR, "output")
LOG_DIR: str = os.path.join(DATA_DIR, "logs")

# ── LLM Provider ───────────────────────────────────────────────────────────────
# Supported: "openai" | "anthropic" | "ollama" | "azure"
LLM_PROVIDER: str = _get_secret("LLM_PROVIDER", "openai")
