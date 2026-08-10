"""
pages/2_Review.py — Review & Export

Loads the routing session produced by the pipeline page.
Displays all questions grouped by tier. AUTO answers are shown in full.
REVIEW and HUMAN answers are editable. Exports a clean Q&A Word document.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import io
import json
from datetime import datetime

import streamlit as st
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

from pipeline.router import load_session, DEFAULT_SESSION_PATH
from pipeline.orchestrator import run_merge

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIER_CONFIG = {
    "AUTO":   {"label": "✅ Auto",   "color": "green",  "badge": "AUTO"},
    "REVIEW": {"label": "🔶 Review", "color": "orange", "badge": "REVIEW"},
    "HUMAN":  {"label": "🔴 Human",  "color": "red",    "badge": "HUMAN"},
}


def _init_state(session: dict) -> None:
    """Populate session_state with editable answer data from the session JSON."""
    if "answers" in st.session_state:
        return  # Already initialised

    answers = {}
    idx = 0

    for tier in ("auto", "review", "human"):
        for item in session.get(tier, []):
            answers[idx] = {
                "question":         item["question"],
                "answer":           item["answer"],
                "original_answer":  item["answer"],
                "matched_question": item.get("matched_question", ""),
                "routing":          item["routing"],
                "confidence":       item["confidence"],
                "source":           item.get("source", "Unknown"),
                "reason":           item.get("reason", ""),
                "vector_score":     item.get("vector_score", 0.0),
                "doc_order":        item.get("doc_order", idx),
                "date":             item.get("date", ""),
                "candidates":       item.get("candidates", []),
                "approved":         tier == "auto",
            }
            idx += 1

    st.session_state["answers"]     = answers
    st.session_state["total_items"] = idx


def _render_question_card(idx: int, item: dict, editable: bool) -> None:
    """Render one question card with answer display or edit area."""
    routing = item["routing"]
    cfg     = TIER_CONFIG[routing]

    with st.container(border=True):

        # Header row
        col_q, col_badge = st.columns([6, 1])
        with col_q:
            st.markdown(f"**Q: {item['question']}**")
        with col_badge:
            if routing == "AUTO":
                st.success(cfg["badge"])
            elif routing == "REVIEW":
                st.warning(cfg["badge"])
            else:
                st.error(cfg["badge"])

        # Confidence + source metadata
        with st.expander("Match details", expanded=False):
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric(
                "Confidence", f"{item['confidence']}%",
                help="LLM-assigned score (0–100) reflecting how well the matched answer addresses this question. ≥ 80 = AUTO, 50–79 = REVIEW, < 50 = HUMAN.",
            )
            mc2.metric(
                "Vector Score", f"{item['vector_score']:.3f}",
                help="Cosine similarity score from the Qdrant vector search (0–1). Measures semantic closeness between the incoming question and the matched answer embedding.",
            )
            mc3.markdown(f"**Source**  \n{item['source']}")
            if item.get("matched_question"):
                st.markdown(f"**Matched question:** {item['matched_question']}")
            if item["reason"]:
                st.caption(f"LLM reasoning: {item['reason']}")

        # Close candidate selection (when 2+ answers within tiebreak threshold)
        candidates = item.get("candidates", [])
        if len(candidates) > 1:
            st.markdown("**⚠️ Multiple close matches found — select to preview each answer:**")
            labels = []
            for cand in candidates:
                date_str = f" · {cand['date']}" if cand.get("date") else " · No date"
                labels.append(
                    f"Score: {cand['confidence']}%{date_str} — {cand.get('source', 'Unknown')}"
                )
            selected_label = st.radio(
                "Candidates",
                labels,
                key=f"cand_{idx}",
                index=0,
                label_visibility="collapsed",
            )
            selected_i     = labels.index(selected_label)
            selected_cand  = candidates[selected_i]

            # Live preview of the selected candidate's answer
            st.markdown("**Preview:**")
            st.info(selected_cand["answer"])

            # Load into answer field (deletes key so text_area re-initialises)
            if st.button("↓ Load this answer into edit field", key=f"load_cand_{idx}"):
                if f"answer_{idx}" in st.session_state:
                    del st.session_state[f"answer_{idx}"]
                st.session_state["answers"][idx]["answer"] = selected_cand["answer"]
                st.rerun()

        # Answer area — always editable
        new_answer = st.text_area(
            "Answer",
            value=item["answer"],
            height=180,
            key=f"answer_{idx}",
            label_visibility="collapsed",
        )
        st.session_state["answers"][idx]["answer"] = new_answer

        col_approve, col_reset = st.columns([3, 1])
        with col_approve:
            approved = st.checkbox(
                "Mark as approved",
                value=item.get("approved", False),
                key=f"approved_{idx}",
            )
            st.session_state["answers"][idx]["approved"] = approved
        with col_reset:
            if st.button("Reset", key=f"reset_{idx}", help="Restore original matched answer"):
                if f"answer_{idx}" in st.session_state:
                    del st.session_state[f"answer_{idx}"]
                st.session_state["answers"][idx]["answer"] = item["original_answer"]
                st.session_state["answers"][idx]["approved"] = False
                st.rerun()

        # Library search
        with st.expander("🔍 Search Library", expanded=False):
            results_key = f"search_results_{idx}"

            query = st.text_input(
                "Search keywords or rephrase the question",
                key=f"search_query_{idx}",
                placeholder="e.g. firm background history founded",
            )

            if st.button("Search", key=f"search_btn_{idx}", type="secondary"):
                if query.strip():
                    with st.spinner("Searching..."):
                        try:
                            from pipeline.matcher import search_library
                            results = search_library(query.strip(), top_k=5)
                            st.session_state[results_key] = results
                        except Exception as e:
                            st.error(f"Search failed: {e}")
                else:
                    st.warning("Enter a search term above.")

            results = st.session_state.get(results_key, [])
            if results:
                st.markdown("**Results — click Use to apply:**")
                for r_idx, result in enumerate(results):
                    with st.container(border=True):
                        rc1, rc2 = st.columns([8, 1])
                        with rc1:
                            st.markdown(f"**{result['question']}**")
                            st.caption(
                                f"Source: {result['source']}  ·  Score: {result['vector_score']:.3f}"
                                + (f"  ·  {result['date']}" if result.get("date") else "")
                            )
                            st.markdown(
                                result["answer"][:300] +
                                ("..." if len(result["answer"]) > 300 else "")
                            )
                        with rc2:
                            if st.button("Use", key=f"use_{idx}_{r_idx}"):
                                if f"answer_{idx}" in st.session_state:
                                    del st.session_state[f"answer_{idx}"]
                                st.session_state["answers"][idx]["answer"]   = result["answer"]
                                st.session_state["answers"][idx]["approved"] = False
                                st.session_state[results_key] = []
                                st.rerun()


def _build_export_doc(answers: dict, source_filename: str) -> bytes:
    """Generate a Word document containing all approved Q&A pairs."""
    doc = Document()

    # Title
    title = doc.add_heading("RFP Draft Responses", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT

    doc.add_paragraph(
        f"Source RFP: {source_filename}    |    "
        f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}"
    ).runs[0].font.size = Pt(9)

    doc.add_paragraph("")

    # Sections by tier
    for tier_label, tier_key in [("Auto Matched", "AUTO"), ("Reviewed", "REVIEW"), ("Manually Answered", "HUMAN")]:
        tier_items = sorted(
            [(idx, item) for idx, item in answers.items()
             if item["routing"] == tier_key and item.get("approved", False)],
            key=lambda x: x[1].get("doc_order", x[0]),
        )
        if not tier_items:
            continue

        doc.add_heading(tier_label, level=2)

        for _, item in tier_items:
            # Question
            q_para = doc.add_paragraph()
            q_run  = q_para.add_run(item["question"])
            q_run.bold      = True
            q_run.font.size = Pt(11)

            # Answer
            a_para = doc.add_paragraph(item["answer"] or "[No answer provided]")
            a_para.paragraph_format.space_after = Pt(14)

        doc.add_paragraph("")

    # Write to bytes buffer
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("🔍 Review & Export")
st.markdown(
    "Review matched answers, edit where needed, then export a clean Q&A document."
)
st.divider()

# Load session
session = load_session(DEFAULT_SESSION_PATH)

if session is None:
    st.info("No pipeline session found. Run the pipeline first.")
    st.page_link("pages/1_Pipeline.py", label="Go to Run Pipeline →", icon="⚡")
    st.stop()

_init_state(session)

source_filename = st.session_state.get("source_filename", "Unknown RFP")
answers         = st.session_state["answers"]

# Summary bar
summary = session.get("summary", {})
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total",    summary.get("total",  0))
c2.metric("✅ Auto",  summary.get("auto",   0))
c3.metric("🔶 Review", summary.get("review", 0))
c4.metric("🔴 Human", summary.get("human",  0))

approved_count = sum(1 for i in answers.values() if i.get("approved", False))
st.progress(approved_count / max(len(answers), 1), text=f"{approved_count} of {len(answers)} approved")

st.divider()

# ---------------------------------------------------------------------------
# All questions in document order
# ---------------------------------------------------------------------------

sorted_items = sorted(answers.items(), key=lambda x: x[1].get("doc_order", x[0]))

for idx, item in sorted_items:
    _render_question_card(idx, item, editable=True)

st.divider()

# ---------------------------------------------------------------------------
# Merge into original RFP
# ---------------------------------------------------------------------------

st.subheader("Merge into Original RFP")

original_bytes = st.session_state.get("original_docx_bytes")

if not original_bytes:
    st.info(
        "The original document isn't available for this session (it may "
        "predate this feature, or the session was loaded from a saved "
        "file). Re-run this RFP through **Run Pipeline** to enable merging, "
        "or use the plain export below instead."
    )
else:
    approved_for_merge = [
        {"question": item["question"], "answer": item["answer"]}
        for item in answers.values()
        if item.get("approved", False) and (item.get("answer") or "").strip()
    ]

    st.markdown(
        f"Insert **{len(approved_for_merge)}** approved answer(s) directly into "
        f"a copy of the original document. Anything the tool can't confidently "
        f"place — table fields, ambiguous structure, unmatched questions — "
        f"comes back separately as an addendum, never guessed at."
    )

    merge_disabled = len(approved_for_merge) == 0
    if merge_disabled:
        st.caption("Approve at least one answer above to enable merging.")

    if st.button(
        "🔀 Merge & Export",
        type="primary",
        use_container_width=True,
        disabled=merge_disabled,
    ):
        with st.spinner("Locating answer slots and merging..."):
            try:
                merge_result = run_merge(
                    original_bytes,
                    source_filename,
                    approved_for_merge,
                    verbose=False,
                )
                st.session_state["merge_result"] = merge_result
            except Exception as e:
                st.error(f"Merge failed: {e}")
                st.stop()

    merge_result = st.session_state.get("merge_result")
    if merge_result is not None:
        st.success(
            f"Merge complete. {merge_result.inserted_count} of "
            f"{merge_result.total_questions} approved answer(s) inserted "
            f"directly into the document."
        )

        col_merged, col_addendum = st.columns(2)
        with col_merged:
            st.download_button(
                label="⬇️ Download Merged RFP",
                data=merge_result.merged_docx_bytes,
                file_name=merge_result.merged_filename,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        with col_addendum:
            st.download_button(
                label=f"⬇️ Download Addendum ({merge_result.skipped_count})",
                data=merge_result.addendum_docx_bytes,
                file_name=merge_result.addendum_filename,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
                disabled=not merge_result.has_addendum_items,
            )

        if merge_result.has_addendum_items:
            st.caption(
                f"Addendum breakdown — "
                f"table fields: {merge_result.addendum_counts.get('table', 0)}, "
                f"needs manual placement: {merge_result.addendum_counts.get('manual_placement', 0)}, "
                f"missing answers: {merge_result.addendum_counts.get('missing_answer', 0)}."
            )

st.divider()

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

st.subheader("Export")

unapproved = [i for i in answers.values() if not i.get("approved", False)]
if unapproved:
    st.warning(
        f"{len(unapproved)} answer(s) not yet approved. "
        "You can still export — unapproved items will be excluded."
    )

export_all = st.checkbox(
    "Include unapproved items in export (marked as [NEEDS REVIEW])",
    value=False,
)

if export_all:
    # Temporarily mark all as approved for export
    export_answers = {
        idx: {**item, "approved": True, "answer": item["answer"] or "[NEEDS REVIEW]"}
        for idx, item in answers.items()
    }
else:
    export_answers = answers

if st.button("📄 Generate Export Document", type="primary", use_container_width=True):
    doc_bytes = _build_export_doc(export_answers, source_filename)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename  = f"RFP_Draft_{timestamp}.docx"

    st.download_button(
        label="⬇️ Download Word Document",
        data=doc_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True,
    )

    st.success(
        "Document ready. Once you've filled the answers into the original RFP format "
        "and it's been reviewed, upload the completed document to the **Database** page "
        "to add it to the answer library."
    )
    st.page_link("pages/3_Database.py", label="Go to Database →", icon="🗄️")