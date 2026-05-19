"""
ingest/embedder.py

Embeds extracted Q&A pairs and upserts them into Qdrant.

Design principles:
  - Embeds QUESTIONS (not answers). Answers are stored in payload.
    Incoming RFP questions are also embedded at match time, so question-to-question
    vector search finds semantically similar stored questions regardless of exact wording.
  - Uses OpenAI text-embedding-3-small (1536 dims, cost-efficient).
  - Idempotent: re-running on the same source file replaces existing points
    (matched by source filename) rather than creating duplicates.
  - Validates pairs before embedding: skips empty/too-short answers.

Usage:
  python embedder.py <path_to_rfp.docx>        # single file
  python embedder.py <directory_of_docs/>       # batch mode
"""

import os
import sys
import uuid
import json
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
)

# Allow running from repo root or from ingest/
sys.path.append(str(Path(__file__).resolve().parent.parent))
from ingest.extractor import extract_qa_pairs

load_dotenv()


# ---------------------------------------------------------------------------
# Configuration (reads from .env or environment)
# ---------------------------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "past_rfp_answers")

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536
MIN_ANSWER_LENGTH = 20       # characters — skip trivial answers
BATCH_SIZE = 50              # points per upsert call
EMBED_RETRY_LIMIT = 3
EMBED_RETRY_DELAY = 2.0      # seconds


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def _get_openai_client() -> OpenAI:
    os.environ.pop("SSL_CERT_FILE", None)   # ← add this line
    if not OPENAI_API_KEY:
        raise EnvironmentError("OPENAI_API_KEY is not set.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _get_qdrant_client() -> QdrantClient:
    if not QDRANT_URL or not QDRANT_API_KEY:
        raise EnvironmentError("QDRANT_URL or QDRANT_API_KEY is not set.")
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


# ---------------------------------------------------------------------------
# Collection management
# ---------------------------------------------------------------------------

def ensure_collection(client: QdrantClient) -> None:
    """Create the collection if it doesn't exist. Never deletes existing data."""
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=EMBEDDING_DIMS,
                distance=Distance.COSINE,
            ),
        )

        print(f"[INFO] Created collection '{COLLECTION_NAME}'.")
    else:
        print(f"[INFO] Collection '{COLLECTION_NAME}' already exists.")
    
    # Ensure payload index exists for source filtering (idempotent)
    client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="source",
        field_schema="keyword",
    )

def delete_points_for_source(client: QdrantClient, source_filename: str) -> None:
    """Remove all existing points from this source file (for idempotent re-ingest)."""
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                FieldCondition(
                    key="source",
                    match=MatchValue(value=source_filename),
                )
            ]
        ),
    )
    print(f"[INFO] Cleared existing points for source: {source_filename}")


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed_text(oai: OpenAI, text: str) -> list[float]:
    """Embed a single text string with retry logic."""
    for attempt in range(1, EMBED_RETRY_LIMIT + 1):
        try:
            response = oai.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            if attempt == EMBED_RETRY_LIMIT:
                raise
            print(f"[WARN] Embedding attempt {attempt} failed: {e}. Retrying...")
            time.sleep(EMBED_RETRY_DELAY * attempt)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_pair(pair: dict) -> tuple[bool, str]:
    """Return (is_valid, reason). Logs rejection reason for debugging."""
    if not pair.get("answer"):
        return False, "empty answer"
    if len(pair["answer"]) < MIN_ANSWER_LENGTH:
        return False, f"answer too short ({len(pair['answer'])} chars)"
    if not pair.get("question"):
        return False, "empty question"
    return True, ""


# ---------------------------------------------------------------------------
# Main ingest function
# ---------------------------------------------------------------------------

def ingest_file(file_path: str, verbose: bool = True, date: str = "") -> dict:
    """
    Full pipeline: extract → validate → embed → upsert for a single file.

    Returns a summary dict with counts.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    print(f"\n{'='*60}")
    print(f"Processing: {path.name}")
    print(f"{'='*60}")

    # 1. Extract
    print("[1/4] Extracting Q&A pairs...")
    pairs = extract_qa_pairs(str(path))
    print(f"      Found {len(pairs)} raw pairs.")

    # 2. Validate
    print("[2/4] Validating pairs...")
    valid_pairs = []
    skipped = []
    for pair in pairs:
        ok, reason = validate_pair(pair)
        if ok:
            valid_pairs.append(pair)
        else:
            skipped.append((pair.get("question", "")[:60], reason))

    if skipped and verbose:
        print(f"      Skipped {len(skipped)} pairs:")
        for q, r in skipped:
            print(f"        - [{r}] {q}...")
    print(f"      {len(valid_pairs)} pairs will be embedded.")

    if not valid_pairs:
        print("[WARN] No valid pairs to embed. Exiting.")
        return {"file": path.name, "extracted": len(pairs), "embedded": 0, "skipped": len(skipped)}

    # 3. Connect
    print("[3/4] Connecting to Qdrant and OpenAI...")
    oai = _get_openai_client()
    qdrant = _get_qdrant_client()
    ensure_collection(qdrant)

    # Idempotent: remove old points for this source
    delete_points_for_source(qdrant, path.name)

    # 4. Embed + upsert in batches
    print(f"[4/4] Embedding and uploading {len(valid_pairs)} pairs...")
    points = []
    for idx, pair in enumerate(valid_pairs, 1):
        if verbose and idx % 10 == 0:
            print(f"      Embedding {idx}/{len(valid_pairs)}...")

        vector = embed_text(oai, pair["question"])
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "question": pair["question"],
                    "answer": pair["answer"],
                    "source": pair["source"],
                    "date": date,
                },
            )
        )

        # Batch upsert
        if len(points) >= BATCH_SIZE:
            qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
            points = []

    # Final batch
    if points:
        qdrant.upsert(collection_name=COLLECTION_NAME, points=points)

    print(f"\n✅ Done. Embedded {len(valid_pairs)} pairs from '{path.name}'.")
    return {
        "file": path.name,
        "extracted": len(pairs),
        "embedded": len(valid_pairs),
        "skipped": len(skipped),
    }


def ingest_pairs(pairs: list[dict], source: str, date: str = "", verbose: bool = False) -> dict:
    """
    Validate, embed, and upsert a pre-filtered list of Q&A pairs.

    Used by the Database UI after the user has reviewed and removed bad pairs.
    Bypasses extraction (pairs are already in memory) but runs the same
    validate → embed → upsert pipeline as ingest_file.

    Args:
        pairs:   List of {"question": str, "answer": str} dicts.
        source:  Source filename to store in the Qdrant payload.
        date:    ISO date string (e.g. "2024-01-15") — user-supplied document date.
        verbose: Print progress during embedding.

    Returns:
        Summary dict with extracted / embedded / skipped counts.
    """
    # Validate
    valid_pairs, skipped = [], []
    for pair in pairs:
        # Ensure source is set
        pair.setdefault("source", source)
        ok, reason = validate_pair(pair)
        if ok:
            valid_pairs.append(pair)
        else:
            skipped.append((pair.get("question", "")[:60], reason))

    if not valid_pairs:
        print("[WARN] No valid pairs to embed.")
        return {"file": source, "extracted": len(pairs), "embedded": 0, "skipped": len(skipped)}

    oai    = _get_openai_client()
    qdrant = _get_qdrant_client()
    ensure_collection(qdrant)
    delete_points_for_source(qdrant, source)

    points = []
    for idx, pair in enumerate(valid_pairs, 1):
        if verbose and idx % 10 == 0:
            print(f"      Embedding {idx}/{len(valid_pairs)}...")
        vector = embed_text(oai, pair["question"])
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "question": pair["question"],
                    "answer":   pair["answer"],
                    "source":   source,
                    "date":     date,
                },
            )
        )
        if len(points) >= BATCH_SIZE:
            qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
            points = []

    if points:
        qdrant.upsert(collection_name=COLLECTION_NAME, points=points)

    print(f"✅ Done. Embedded {len(valid_pairs)} pairs from '{source}'.")
    return {
        "file":      source,
        "extracted": len(pairs),
        "embedded":  len(valid_pairs),
        "skipped":   len(skipped),
    }


def ingest_directory(dir_path: str) -> list[dict]:
    """Batch ingest all .doc and .docx files in a directory."""
    dir_path = Path(dir_path)
    files = list(dir_path.glob("*.docx")) + list(dir_path.glob("*.doc"))

    if not files:
        print(f"[WARN] No .doc/.docx files found in {dir_path}")
        return []

    print(f"Found {len(files)} file(s) to ingest.")
    results = []
    for f in files:
        try:
            result = ingest_file(str(f))
            results.append(result)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ERROR] Failed to ingest {f.name}: {e}")
            results.append({"file": f.name, "error": str(e)})

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python embedder.py <file.docx>         # single file")
        print("  python embedder.py <directory/>        # batch")
        sys.exit(1)

    target = sys.argv[1]
    path = Path(target)

    if path.is_dir():
        results = ingest_directory(target)
    elif path.is_file():
        results = [ingest_file(target)]
    else:
        print(f"[ERROR] Not a valid file or directory: {target}")
        sys.exit(1)

    print("\n--- Ingest Summary ---")
    for r in results:
        if "error" in r:
            print(f"  ❌ {r['file']}: {r['error']}")
        else:
            print(f"  ✅ {r['file']}: {r['embedded']} embedded, {r['skipped']} skipped")
