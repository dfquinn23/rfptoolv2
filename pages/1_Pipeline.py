"""
pages/1_Pipeline.py — Run Pipeline

Upload an incoming (unanswered) RFP, detect questions,
match against the library, and route by confidence tier.
Results are saved to pipeline/routing_session.json for the Review page.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile
import streamlit as st
from pipeline.question_detector import detect_questions
from pipeline.router import route_rfp, save_session

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.title("⚡ Run Pipeline")
st.markdown(
    "Upload an incoming RFP document. The tool will detect all questions, "
    "match each one against the answer library, and sort results by confidence."
)
st.divider()

# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

uploaded_file = st.file_uploader(
    "Upload incoming RFP (.docx)",
    type=["docx"],
    help="Upload the blank or unanswered RFP you need to respond to.",
)

if uploaded_file is None:
    st.info("Upload a .docx file above to get started.")
    st.stop()

st.success(f"**{uploaded_file.name}** uploaded successfully.")

verbose = st.checkbox("Verbose output (show per-question matching detail)", value=False)

run = st.button("▶ Run Pipeline", type="primary", use_container_width=True)

if not run:
    st.stop()

# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

# Write uploaded file to a temp location so detect_questions can read it
with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
    tmp.write(uploaded_file.read())
    tmp_path = tmp.name

st.divider()

with st.status("Running pipeline...", expanded=True) as status:

    # Stage 1 — Question detection
    st.write("🔍 Detecting questions...")
    try:
        questions = detect_questions(tmp_path, verbose=False)
    except Exception as e:
        status.update(label="Pipeline failed.", state="error")
        st.error(f"Question detection failed: {e}")
        st.stop()

    if not questions:
        status.update(label="No questions found.", state="error")
        st.warning("No questions were detected in this document. Check the file and try again.")
        st.stop()

    st.write(f"✅ {len(questions)} question(s) detected.")

    # Stage 2 — Matching & routing
    st.write("🧠 Matching questions against the library...")
    try:
        output = route_rfp(questions, verbose=verbose)
    except Exception as e:
        status.update(label="Pipeline failed.", state="error")
        st.error(f"Matching failed: {e}")
        st.stop()

    # Stage 3 — Save session
    st.write("💾 Saving session...")
    try:
        session_path = save_session(output)
        # Store source filename in session state for the Review page
        st.session_state["source_filename"] = uploaded_file.name
        st.session_state["session_ready"]   = True
    except Exception as e:
        status.update(label="Pipeline failed.", state="error")
        st.error(f"Could not save session: {e}")
        st.stop()

    status.update(label="Pipeline complete.", state="complete")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Results")

total = output.total
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Questions", total)
col2.metric("✅ Auto",   len(output.auto),   help="Confidence ≥ 80 — safe to use verbatim")
col3.metric("🔶 Review", len(output.review), help="Confidence 50–79 — human spot-check recommended")
col4.metric("🔴 Human",  len(output.human),  help="Confidence < 50 — needs manual answer")

st.divider()

if len(output.review) > 0 or len(output.human) > 0:
    st.warning(
        f"{len(output.review) + len(output.human)} question(s) need attention. "
        "Head to **Review & Export** to work through them."
    )
else:
    st.success("All questions matched with high confidence. Ready to export.")

st.page_link("pages/2_Review.py", label="Go to Review & Export →", icon="🔍")
