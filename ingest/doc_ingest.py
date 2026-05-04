# ingest/doc_ingest.py
# Ingestion pipeline for company documents (ADV, pitch decks, policies, factsheets).
# These go into the company_docs collection and serve as the Tier 2 fallback.

import os
import uuid
from openai import OpenAI
from qdrant_client.models import PointStruct
from docx import Document
from core.config import (
    OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL,
    COLLECTION_COMPANY_DOCS, DOC_CHUNK_SIZE, DOC_CHUNK_OVERLAP,
)
from core.qdrant_client import get_client
from ingest.cleaner import normalize_text

openai_client = OpenAI(api_key=OPENAI_API_KEY)


def _extract_text_from_docx(path: str) -> str:
    doc = Document(path)
    return "\n\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())


def _chunk_text(text: str, chunk_size: int = DOC_CHUNK_SIZE, overlap: int = DOC_CHUNK_OVERLAP) -> list[str]:
    """
    Simple word-based chunking with overlap.
    chunk_size and overlap are in approximate word counts.
    """
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def ingest_company_doc(path: str) -> int:
    """
    Extract, chunk, embed, and upsert a company document.
    Returns count of chunks upserted.
    """
    from datetime import datetime
    qdrant = get_client()
    filename = os.path.basename(path)
    timestamp = datetime.utcnow().isoformat()

    raw = _extract_text_from_docx(path)
    raw = normalize_text(raw)
    chunks = _chunk_text(raw)

    points = []
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        response = openai_client.embeddings.create(
            model=OPENAI_EMBEDDING_MODEL,
            input=chunk,
        )
        vector = response.data[0].embedding
        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "text": chunk,
                "source": filename,
                "chunk_index": i,
                "ingested_at": timestamp,
            },
        ))

    if points:
        qdrant.upsert(collection_name=COLLECTION_COMPANY_DOCS, points=points)

    return len(points)
