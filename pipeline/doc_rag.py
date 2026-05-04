# pipeline/doc_rag.py
# Tier 2 fallback: RAG over the company_docs collection.
# Called only when Tier 1 (past RFP answers) scores below threshold.
# The LLM generates a grounded answer from retrieved chunks — clearly labeled as AI Generated.

from openai import OpenAI
from core.config import (
    OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL, OPENAI_LLM_MODEL,
    COLLECTION_COMPANY_DOCS, RETRIEVAL_TOP_K,
)
from core.qdrant_client import get_client

openai_client = OpenAI(api_key=OPENAI_API_KEY)

RAG_SYSTEM_PROMPT = """You are helping to draft a response to a question in an investment management RFP.

You have been given relevant excerpts from the firm's internal documents (ADV filings, pitch decks, policies, factsheets).

Instructions:
- Write a professional, concise answer using ONLY the information in the provided excerpts.
- Do not invent facts, statistics, or claims not present in the excerpts.
- If the excerpts do not contain enough information to answer the question, say: "Insufficient information available — manual response required."
- Write in third person ("The firm...", "Our approach...").
- Keep the tone professional and appropriate for an institutional investor audience."""


def _get_embedding(text: str) -> list[float]:
    response = openai_client.embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def doc_rag_answer(question: str) -> dict | None:
    """
    Search company_docs collection and generate a grounded answer.
    Returns {answer, source, score} or None if no relevant chunks found.
    """
    qdrant = get_client()
    vector = _get_embedding(question)

    try:
        results = qdrant.search(
            collection_name=COLLECTION_COMPANY_DOCS,
            query_vector=vector,
            limit=RETRIEVAL_TOP_K,
            with_payload=True,
        )
    except Exception:
        return None

    if not results or results[0].score < 0.50:
        return None

    # Compile context from top chunks
    context_parts = []
    sources = []
    for r in results:
        payload = r.payload or {}
        text = payload.get("text", "")
        source = payload.get("source", "unknown")
        if text:
            context_parts.append(text)
            sources.append(source)

    if not context_parts:
        return None

    context = "\n\n---\n\n".join(context_parts)
    user_message = f"Question: {question}\n\nExcerpts from firm documents:\n\n{context}"

    from pipeline.llm_provider import chat_completion
    answer = chat_completion(RAG_SYSTEM_PROMPT, user_message, temperature=0.2)

    if "insufficient information" in answer.lower():
        return None

    return {
        "answer": answer,
        "source": ", ".join(set(sources)),
        "score": results[0].score,
    }
