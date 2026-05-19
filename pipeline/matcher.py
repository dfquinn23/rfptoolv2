"""
pipeline/matcher.py

Two-stage matching pipeline for incoming RFP questions.

Stage 1 — Vector search: embed the question, retrieve top N candidates from Qdrant.
Stage 2 — LLM judge: evaluate each candidate's relevance, select the best match.

The LLM never generates answers. Its only role is to evaluate which stored answer
best addresses the incoming question. The returned answer is always verbatim from
the knowledge base.

Routing tiers (based on confidence score 0-100):
  ≥ 80  → AUTO     : insert answer directly
  50-79 → REVIEW   : surface for light human review
  < 50  → HUMAN    : no good match, needs manual answer
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
QDRANT_URL       = os.getenv("QDRANT_URL")
QDRANT_API_KEY   = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME  = os.getenv("COLLECTION_NAME", "past_rfp_answers")

EMBEDDING_MODEL  = "text-embedding-3-small"
JUDGE_MODEL      = "gpt-4o-mini"
CANDIDATE_COUNT  = 5       # how many candidates to pull from Qdrant

THRESHOLD_AUTO   = 80      # confidence ≥ 80 → auto-insert
THRESHOLD_REVIEW = 50      # confidence 50-79 → light review
                           # confidence < 50  → human queue
TIEBREAK_THRESHOLD = 5     # surface all candidates within this many points of top score

JUDGE_PROMPT = """You are evaluating whether a stored answer from a completed RFP 
adequately addresses an incoming RFP question.

Incoming question:
{question}

Candidate answer:
{answer}

Score how well this answer addresses the question on a scale of 0 to 100:
- 90-100: The answer directly and completely addresses the question.
- 70-89:  The answer addresses the question well with minor gaps.
- 50-69:  The answer partially addresses the question but is missing key points.
- 25-49:  The answer is tangentially related but does not directly answer the question.
- 0-24:   The answer is unrelated to the question.

Reply with ONLY a JSON object in this format: {{"score": <integer 0-100>, "reason": "<one sentence>"}}"""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    question:         str    # incoming question
    answer:           str    # verbatim answer from knowledge base
    matched_question: str    # the question this answer was originally paired with
    source:           str    # source document filename
    confidence:       int    # 0-100
    routing:          str    # "AUTO", "REVIEW", or "HUMAN"
    reason:           str    # LLM's one-sentence explanation
    vector_score:     float  # raw Qdrant cosine similarity score
    date:             str              = ""                   # document date from payload
    candidates:       list             = field(default_factory=list)  # close competitors


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def _get_clients():
    os.environ.pop("SSL_CERT_FILE", None)
    oai    = OpenAI(api_key=OPENAI_API_KEY)
    qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return oai, qdrant


# ---------------------------------------------------------------------------
# Stage 1: Vector search
# ---------------------------------------------------------------------------

def _embed_question(oai: OpenAI, question: str) -> list[float]:
    """Embed the incoming question for vector search."""
    response = oai.embeddings.create(
        model=EMBEDDING_MODEL,
        input=question,
    )
    return response.data[0].embedding


def _vector_search(qdrant: QdrantClient, vector: list[float]) -> list[dict]:
    """
    Retrieve top N candidates from Qdrant.
    Returns list of {question, answer, source, vector_score}.
    """
    results = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        limit=CANDIDATE_COUNT,
        with_payload=True,
    ).points

    candidates = []
    for r in results:
        candidates.append({
            "question":     r.payload.get("question", ""),
            "answer":       r.payload.get("answer", ""),
            "source":       r.payload.get("source", ""),
            "date":         r.payload.get("date", ""),
            "vector_score": round(r.score, 4),
        })

    return candidates


# ---------------------------------------------------------------------------
# Stage 2: LLM judge
# ---------------------------------------------------------------------------

def _score_candidate(oai: OpenAI, question: str, candidate: dict) -> tuple[int, str]:
    """
    Ask the LLM to score how well the candidate answer addresses the question.
    Returns (score: int, reason: str).
    """
    import json

    prompt = JUDGE_PROMPT.format(
        question=question,
        answer=candidate["answer"],
    )

    response = oai.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=150,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()

    try:
        parsed = json.loads(raw)
        score  = int(parsed.get("score", 0))
        reason = str(parsed.get("reason", ""))
        score  = max(0, min(100, score))  # clamp to 0-100
    except Exception:
        score  = 0
        reason = "Failed to parse LLM score."

    return score, reason


def _route(confidence: int) -> str:
    if confidence >= THRESHOLD_AUTO:
        return "AUTO"
    elif confidence >= THRESHOLD_REVIEW:
        return "REVIEW"
    else:
        return "HUMAN"


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def match_question(question: str, verbose: bool = False) -> MatchResult:
    """
    Find the best matching answer for an incoming RFP question.

    Args:
        question: The incoming RFP question text.
        verbose:  Print scoring details for each candidate.

    Returns:
        MatchResult with the best answer, confidence score, and routing decision.
    """
    oai, qdrant = _get_clients()

    # Stage 1: vector search
    vector     = _embed_question(oai, question)
    candidates = _vector_search(qdrant, vector)

    if not candidates:
        return MatchResult(
            question=question,
            answer="",
            matched_question="",
            source="",
            confidence=0,
            routing="HUMAN",
            reason="No candidates found in knowledge base.",
            vector_score=0.0,
        )

    # Stage 2: LLM judge — score each candidate
    scored = []
    for i, candidate in enumerate(candidates, 1):
        score, reason = _score_candidate(oai, question, candidate)
        if verbose:
            print(f"  Candidate {i} | vector={candidate['vector_score']:.3f} "
                  f"| llm={score} | {candidate['answer'][:60]}...")
        scored.append((score, reason, candidate))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score = scored[0][0]

    # Build list of candidates within TIEBREAK_THRESHOLD of the top score
    close = [
        {
            "answer":           c["answer"],
            "matched_question": c["question"],
            "source":           c["source"],
            "confidence":       s,
            "vector_score":     c["vector_score"],
            "date":             c.get("date", ""),
            "reason":           r,
        }
        for s, r, c in scored
        if top_score - s <= TIEBREAK_THRESHOLD
    ]

    # Sort close candidates by date descending — most recent first, undated last
    close.sort(key=lambda x: x.get("date") or "0000-00-00", reverse=True)

    # Primary answer = first after date sort (most recent, or top scorer if no dates)
    primary = close[0]

    # Only surface candidates to the UI when 2+ are within the threshold
    candidates_for_ui = close if len(close) > 1 else []

    return MatchResult(
        question=question,
        answer=primary["answer"],
        matched_question=primary["matched_question"],
        source=primary["source"],
        confidence=primary["confidence"],
        routing=_route(primary["confidence"]),
        reason=primary["reason"],
        vector_score=primary["vector_score"],
        date=primary.get("date", ""),
        candidates=candidates_for_ui,
    )


def search_library(query: str, top_k: int = 5) -> list[dict]:
    """
    Search the library for questions matching a free-text query.
    Used by the Review UI to let reviewers find better answers manually.

    Args:
        query:  Any text — keywords, a rephrased question, topic words.
        top_k:  Number of results to return.

    Returns:
        List of candidate dicts with question, answer, source, vector_score.
    """
    oai, qdrant = _get_clients()
    vector      = _embed_question(oai, query)

    results = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        limit=top_k,
        with_payload=True,
    ).points

    return [
        {
            "question":     r.payload.get("question", ""),
            "answer":       r.payload.get("answer", ""),
            "source":       r.payload.get("source", ""),
            "date":         r.payload.get("date", ""),
            "vector_score": round(r.score, 4),
        }
        for r in results
    ]


def match_questions(questions: list[str], verbose: bool = False) -> list[MatchResult]:
    """Match a list of questions. Returns results in the same order."""
    return [match_question(q, verbose=verbose) for q in questions]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python matcher.py \"Your RFP question here\"")
        sys.exit(1)

    q      = " ".join(sys.argv[1:])
    result = match_question(q, verbose=True)

    print(f"\n{'='*60}")
    print(f"Question:   {result.question}")
    print(f"Routing:    {result.routing} (confidence: {result.confidence}/100)")
    print(f"Reason:     {result.reason}")
    print(f"Source:     {result.source}")
    print(f"Matched Q:  {result.matched_question[:100]}")
    print(f"Answer:\n{result.answer}")
