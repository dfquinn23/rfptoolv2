# pipeline/router.py
# Confidence tiering — determines the trust label for each matched answer.

from core.config import SCORE_AUTO_INSERT, SCORE_NEEDS_REVIEW


# Trust tier constants
TIER_VERIFIED = "verified"          # ✅ Auto-insert
TIER_NEEDS_REVIEW = "needs_review"  # ⚠️  Insert with review flag
TIER_AI_GENERATED = "ai_generated"  # 🔬 From company docs RAG
TIER_MANUAL = "manual_required"     # 🚫 Human must write from scratch


def assign_tier(final_score: float) -> str:
    """Assign a confidence tier based on the blended match score."""
    if final_score >= SCORE_AUTO_INSERT:
        return TIER_VERIFIED
    elif final_score >= SCORE_NEEDS_REVIEW:
        return TIER_NEEDS_REVIEW
    else:
        return TIER_MANUAL  # Will be upgraded to AI_GENERATED if doc RAG finds something


TIER_LABELS = {
    TIER_VERIFIED: "✅ Verified",
    TIER_NEEDS_REVIEW: "⚠️ Needs Review",
    TIER_AI_GENERATED: "🔬 AI Generated",
    TIER_MANUAL: "🚫 Manual Required",
}

TIER_COLORS = {
    TIER_VERIFIED: "green",
    TIER_NEEDS_REVIEW: "orange",
    TIER_AI_GENERATED: "blue",
    TIER_MANUAL: "red",
}


def route_question(question: str, match: dict | None, doc_fallback_fn=None) -> dict:
    """
    Given a question and its best match (or None), determine the tier and
    optionally call the company docs fallback for low-confidence questions.

    Returns a result dict ready for output:
      question, answer, tier, final_score, source, label
    """
    if match is None:
        score = 0.0
    else:
        score = match.get("final_score", 0.0)

    tier = assign_tier(score)

    if tier == TIER_MANUAL and doc_fallback_fn is not None:
        # Try company docs RAG fallback
        doc_result = doc_fallback_fn(question)
        if doc_result:
            return {
                "question": question,
                "answer": doc_result["answer"],
                "tier": TIER_AI_GENERATED,
                "final_score": doc_result.get("score", 0.0),
                "source": doc_result.get("source", "company_docs"),
                "label": TIER_LABELS[TIER_AI_GENERATED],
            }

    if match:
        return {
            "question": question,
            "answer": match["answer"],
            "tier": tier,
            "final_score": score,
            "source": match.get("source", "unknown"),
            "label": TIER_LABELS[tier],
            "llm_reason": match.get("llm_reason", ""),
        }

    return {
        "question": question,
        "answer": "",
        "tier": TIER_MANUAL,
        "final_score": 0.0,
        "source": "",
        "label": TIER_LABELS[TIER_MANUAL],
    }
