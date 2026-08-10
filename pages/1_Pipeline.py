"""
pages/1_Pipeline.py — Run Pipeline

Flow:
  1. Upload raw incoming RFP
  2. Detect questions → display in editable text area (numbered, one per line)
  3. User edits inline — delete bad items, fix anything
  4. Optionally download a clean copy for records
  5. Click Run Pipeline → parse text area → match → route → save session
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import re
import tempfile
import streamlit as st

from pipeline.question_detector import detect_questions, generate_clean_doc
from pipeline.router import route_rfp, save_session
from core.doc_converter import convert_to_docx, DocConversionError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _questions_to_text(questions: list[str]) -> str:
    """Format a list of questions as a numbered text block."""
    return "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))


def _text_to_questions(text: str) -> list[str]:
    """
    Parse the edited text area back into an ordered list of questions.
    Accepts lines formatted as "N. Question text".
    Blank lines and lines that don't match are ignored.
    """
    questions = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^\d+\.\s+(.+)$", line)
        if match:
            questions.append(match.group(1).strip())
        else:
            # Line has content but no number prefix — include as-is
            # (handles manual additions without numbering)
            questions.append(line)
    return questions


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("⚡ Run Pipeline")
st.markdown(
    "Upload a raw incoming RFP, review and edit the detected questions inline, "
    "then run the pipeline to match each question against the answer library."
)
st.divider()

# Stage tracking
if "pipeline_stage" not in st.session_state:
    st.session_state["pipeline_stage"]     = "upload"
    st.session_state["questions_text"]     = ""
    st.session_state["source_filename"]    = ""
    st.session_state["pipeline_output"]    = None

# ===========================================================================
# STAGE 1 — Upload & detect
# ===========================================================================

if st.session_state["pipeline_stage"] == "upload":

    uploaded = st.file_uploader(
        "Upload raw incoming RFP (.docx or .doc)",
        type=["docx", "doc"],
        help="Upload the RFP exactly as received — extraneous content will be stripped automatically.",
    )

    if uploaded is None:
        st.info("Upload a .docx or .doc file above to get started.")
        st.stop()

    st.success(f"**{uploaded.name}** ready.")

    if st.button("🔍 Detect Questions", type="primary", use_container_width=True):

        suffix = Path(uploaded.name).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(uploaded.read())
            tmp_path = tmp.name

        if suffix == ".doc":
            with st.spinner("Converting legacy .doc to .docx..."):
                try:
                    tmp_path = convert_to_docx(tmp_path)
                except DocConversionError as e:
                    st.error(str(e))
                    st.stop()

        with st.spinner("Detecting questions..."):
            try:
                questions = detect_questions(tmp_path, verbose=False)
            except Exception as e:
                st.error(f"Question detection failed: {e}")
                st.stop()

        if not questions:
            st.warning("No questions detected. Check the file and try again.")
            st.stop()

        # Persist the original (post-conversion) document bytes so later
        # pages — specifically the Merge step on Review — can write approved
        # answers back into this exact document without asking the user to
        # re-upload it.
        with open(tmp_path, "rb") as f:
            st.session_state["original_docx_bytes"] = f.read()

        st.session_state["questions_text"]  = _questions_to_text(questions)
        st.session_state["source_filename"] = uploaded.name
        st.session_state["pipeline_stage"]  = "edit"
        st.rerun()

# ===========================================================================
# STAGE 2 — Edit questions inline
# ===========================================================================

elif st.session_state["pipeline_stage"] == "edit":

    source         = st.session_state["source_filename"]
    questions_text = st.session_state["questions_text"]
    initial_count  = len(_text_to_questions(questions_text))

    st.subheader(f"Review & Edit — {source}")
    st.markdown(
        f"**{initial_count}** question(s) detected. "
        "Edit the list below — delete any lines that aren't real questions, "
        "or fix any wording. Each line should stay in `N. Question text` format."
    )

    # Editable text area — version key forces re-render on renumber
    editor_version = st.session_state.get("editor_version", 0)
    edited_text = st.text_area(
        "Questions",
        value=st.session_state["questions_text"],
        height=500,
        label_visibility="collapsed",
        key=f"questions_editor_{editor_version}",
    )

    # Live count
    current_questions = _text_to_questions(edited_text)
    st.caption(f"{len(current_questions)} question(s) in current list.")

    col_renumber, _ = st.columns([1, 5])
    with col_renumber:
        if st.button("🔢 Renumber", help="Resequence question numbers after deletions"):
            st.session_state["questions_text"]  = _questions_to_text(current_questions)
            st.session_state["editor_version"]  = editor_version + 1
            st.rerun()

    st.divider()

    col_back, col_download, col_run = st.columns([1, 2, 3])

    with col_back:
        if st.button("← Start Over", use_container_width=True):
            st.session_state["pipeline_stage"]  = "upload"
            st.session_state["questions_text"]  = ""
            st.session_state["source_filename"] = ""
            st.session_state["original_docx_bytes"] = None
            st.rerun()

    with col_download:
        # Generate clean doc in memory for optional download
        if current_questions:
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as out_tmp:
                out_path = out_tmp.name
            try:
                generate_clean_doc(current_questions, out_path, source_name=source)
                with open(out_path, "rb") as f:
                    doc_bytes = f.read()
                stem       = Path(source).stem
                clean_name = f"{stem}_clean.docx"
                st.download_button(
                    label="⬇️ Save Clean Copy",
                    data=doc_bytes,
                    file_name=clean_name,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                    help="Download a clean copy of the edited question list for your records.",
                )
            except Exception:
                pass  # Download is optional — don't block the flow

    with col_run:
        if st.button(
            f"▶ Run Pipeline ({len(current_questions)} questions)",
            type="primary",
            use_container_width=True,
            disabled=len(current_questions) == 0,
        ):
            # Persist the final edited list and move to matching
            st.session_state["questions_text"] = edited_text
            st.session_state["pipeline_stage"] = "running"
            st.rerun()

# ===========================================================================
# STAGE 3 — Match & route
# ===========================================================================

elif st.session_state["pipeline_stage"] == "running":

    questions = _text_to_questions(st.session_state["questions_text"])

    with st.status("Running pipeline...", expanded=True) as status:

        st.write(f"🧠 Matching {len(questions)} question(s) against the library...")
        try:
            output = route_rfp(questions, verbose=False)
        except Exception as e:
            status.update(label="Matching failed.", state="error")
            st.error(f"Matching failed: {e}")
            st.stop()

        st.write("💾 Saving session...")
        try:
            save_session(output)
            if "answers" in st.session_state:
                del st.session_state["answers"]
            st.session_state["pipeline_output"] = output
            st.session_state["pipeline_stage"]  = "complete"
        except Exception as e:
            status.update(label="Could not save session.", state="error")
            st.error(f"Save failed: {e}")
            st.stop()

        status.update(label="Pipeline complete.", state="complete")

    st.rerun()

# ===========================================================================
# STAGE 4 — Complete
# ===========================================================================

elif st.session_state["pipeline_stage"] == "complete":

    output = st.session_state["pipeline_output"]
    source = st.session_state["source_filename"]

    st.subheader("Results")
    st.markdown(f"**Source:** {source}")
    st.divider()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Questions", output.total)
    col2.metric("✅ Auto",    len(output.auto),
                help="Confidence ≥ 80 — safe to use verbatim")
    col3.metric("🔶 Review",  len(output.review),
                help="Confidence 50–79 — human spot-check recommended")
    col4.metric("🔴 Human",   len(output.human),
                help="Confidence < 50 — needs manual answer")

    st.divider()

    if len(output.review) > 0 or len(output.human) > 0:
        st.warning(
            f"{len(output.review) + len(output.human)} question(s) need attention. "
            "Head to **Review & Export** to work through them."
        )
    else:
        st.success("All questions matched with high confidence. Ready to export.")

    col_new, col_review = st.columns(2)
    with col_new:
        if st.button("⚡ Run a New RFP", use_container_width=True):
            st.session_state["pipeline_stage"]  = "upload"
            st.session_state["questions_text"]  = ""
            st.session_state["pipeline_output"] = None
            st.session_state["original_docx_bytes"] = None
            st.rerun()
    with col_review:
        st.page_link("pages/2_Review.py", label="Go to Review & Export →", icon="🔍")