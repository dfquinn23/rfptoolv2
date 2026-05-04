# core/qdrant_client.py
# Single cached Qdrant client. Import get_client() everywhere — never instantiate directly.

import streamlit as st
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from core.config import (
    QDRANT_URL, QDRANT_API_KEY,
    COLLECTION_RFP_ANSWERS, COLLECTION_COMPANY_DOCS,
    OPENAI_EMBEDDING_DIMENSIONS,
)


@st.cache_resource(show_spinner=False)
def get_client() -> QdrantClient:
    """Return a cached Qdrant client. Raises on bad credentials."""
    if not QDRANT_URL or not QDRANT_API_KEY:
        raise ValueError(
            "QDRANT_URL and QDRANT_API_KEY must be set in secrets or .env"
        )
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return client


def ensure_collections() -> None:
    """
    Create both collections if they don't exist.
    Safe to call on every startup — no-ops if already present.
    """
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}

    for name in (COLLECTION_RFP_ANSWERS, COLLECTION_COMPANY_DOCS):
        if name not in existing:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=OPENAI_EMBEDDING_DIMENSIONS,
                    distance=Distance.COSINE,
                ),
            )


def collection_stats() -> dict:
    """Return point counts for both collections."""
    client = get_client()
    stats = {}
    for name in (COLLECTION_RFP_ANSWERS, COLLECTION_COMPANY_DOCS):
        try:
            info = client.get_collection(name)
            stats[name] = info.points_count
        except Exception:
            stats[name] = 0
    return stats
