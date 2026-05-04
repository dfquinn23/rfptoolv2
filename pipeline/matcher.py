# pipeline/matcher.py
# Two-stage matching: Qdrant vector pre-filter → LLM-as-judge reranker.
# The LLM SCORES relevance only — it never generates new answer content.

import json
from openai import OpenAI
from qdrant_client.models import ScoredPoint
from core.config import (
    OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL, OPENAI_LLM_MODEL,
    COLLECTION_RFP_ANSWERS, RETRIEVAL_TOP_K,
)
from core.qdrant_client import get_client

openai_client = OpenAI(api_key=OPENAI_API_KEY)

RERANK_SYSTEM_PROMPT = """You are evaluating whether a past RFP answer is relevant to a new question.

Your only job is to score the semantic relevance — do NOT generate new content.

Scoring guide:
  0.90–1.00: The answer directly and completely addresses the question. Could be used as-is.
  0.70–0.89: Substantially relevant; may need minor editing but core content applies.
  0.50–0.69: Partially relevant; addresses related topic but misses key aspects.
  0.00–0.49: Not relevant; different topic or too vague to be useful.

Respond ONLY with a JSON object:
{"score": 0.87, "reason": "One sentence explaining your score."}

No preamble. No markdown fences."""


def get_embedding(text: str) -> list[float]:
    response = openai_client.embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def vector_search(question: str, top_k: int = RETRIEVAL_TOP_K) -> list[ScoredPoint]:
    """Stage 1: Fast vector retrieval of candidate answers."""
    qdrant = get_client()
    vector = get_embedding(question)
    results = qdrant.search(
        collection_name=COLLECTION_RFP_ANSWERS,
        query_vector=vector,
        limit=top_k,
        with_payload=True,
    )
    return results


def rerank_candidate(question: str, candidate_answer: str) -> dict:
    """
    Stage 2: LLM scores one candidate answer for relevance to the question.
    Returns {score, reason}.
    """
    user_message = f"Question: {question}\n\nCandidate Answer: {candidate_answer}"
    from pipeline.llm_provider import chat_completion
    raw = chat_completion(RERANK_SYSTEM_PROMPT, user_message)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"score": 0.0, "reason": "parse error"}


def find_best_match(question: str) -> dict | None:
    """
    Full two-stage match for one question.
    Returns the best match dict or None if no candidates found.

    Return schema:
      question:   str
      answer:     str
      source:     str
      vector_score:  float  (cosine similarity from Qdrant)
      llm_score:     float  (LLM relevance score)
      final_score:   float  (weighted blend)
      llm_reason:    str
    """
    candidates = vector_search(question)
    if not candidates:
        return None

    best = None
    best_final = -1.0

    for candidate in candidates:
        payload = candidate.payload or {}
        answer = payload.get("answer", "")
        if not answer:
            continue

        rerank = rerank_candidate(question, answer)
        llm_score = float(rerank.get("score", 0.0))
        vector_score = candidate.score

        # Weighted blend: 40% vector, 60% LLM judge
        final_score = 0.4 * vector_score + 0.6 * llm_score

        if final_score > best_final:
            best_final = final_score
            best = {
                "question": question,
                "answer": answer,
                "source": payload.get("source", "unknown"),
                "vector_score": round(vector_score, 4),
                "llm_score": round(llm_score, 4),
                "final_score": round(final_score, 4),
                "llm_reason": rerank.get("reason", ""),
            }

    return best
