# ingest/embedder.py
# Embeds validated Q&A pairs and upserts them into Qdrant.
# Embeds the ANSWER text only (the question is stored in payload for display).

import uuid
from openai import OpenAI
from qdrant_client.models import PointStruct
from core.config import OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL, COLLECTION_RFP_ANSWERS
from core.qdrant_client import get_client

openai_client = OpenAI(api_key=OPENAI_API_KEY)


def get_embedding(text: str) -> list[float]:
    """Return an embedding vector for the given text."""
    response = openai_client.embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def embed_and_upsert(pairs: list[dict], batch_size: int = 50) -> int:
    """
    Embed all answer texts and upsert into Qdrant.
    Returns count of successfully upserted points.

    Payload schema (consistent — no more field name drift):
      question:    str
      answer:      str
      source:      str   (filename)
      ingested_at: str   (ISO timestamp)
    """
    from datetime import datetime
    qdrant = get_client()
    timestamp = datetime.utcnow().isoformat()
    total_upserted = 0

    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]
        points = []

        for pair in batch:
            vector = get_embedding(pair["answer"])
            points.append(PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "question": pair["question"],
                    "answer": pair["answer"],
                    "source": pair.get("source", "unknown"),
                    "ingested_at": timestamp,
                },
            ))

        qdrant.upsert(collection_name=COLLECTION_RFP_ANSWERS, points=points)
        total_upserted += len(points)

    return total_upserted
