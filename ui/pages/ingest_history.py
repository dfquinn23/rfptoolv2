# ui/pages/ingest_history.py
# Page 2: Ingest historical RFPs into the knowledge base.
# Shows a preview of extracted Q&A pairs before committing to Qdrant.

import streamlit as st
import os
import shutil
from tempfile import NamedTemporaryFile
from ingest.extractor import extract_qa_pairs
from ingest.validator import validate_pairs, validation_report
from ingest.embedder import embed_and_upsert
from core.config import PAST_RFPS_DIR
from core.logger import log_ingest


def render():
    st.header("📚 Ingest Historical RFP")
    st.write(
        "Upload a completed, human-reviewed RFP. The tool will extract Q&A pairs, "
        "show you a preview for approval, then add them to the knowledge base."
    )
    st.info("📋 Accepts raw RFPs — the LLM extractor handles tables, numbered lists, and mixed formats.")

    uploaded = st.file_uploader("Upload completed .docx RFP", type="docx", key="ingest_rfp")
    if not uploaded:
        return

    if st.button("Extract Q&A Pairs", type="primary"):
        with NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            tmp.write(uploaded.getbuffer())
            tmp_path = tmp.name

        with st.spinner("Extracting Q&A pairs via LLM..."):
            try:
                pairs = extract_qa_pairs(tmp_path)
                valid, skipped = validate_pairs(pairs)
                st.session_state["ingest_valid"] = valid
                st.session_state["ingest_skipped"] = skipped
                st.session_state["ingest_source"] = uploaded.name
                st.session_state["ingest_tmp_path"] = tmp_path
            except Exception as e:
                st.error(f"Extraction failed: {e}")
                return

    # Show preview if extraction has run
    if "ingest_valid" not in st.session_state:
        return

    valid = st.session_state["ingest_valid"]
    skipped = st.session_state["ingest_skipped"]
    source = st.session_state["ingest_source"]

    st.success(f"Extracted **{len(valid)}** valid pairs from _{source}_")
    st.caption(validation_report(valid, skipped))

    if skipped:
        with st.expander(f"⚠️ {len(skipped)} skipped pairs (click to review)"):
            for p in skipped:
                st.markdown(f"**Reason:** `{p.get('skip_reason')}`")
                st.write(f"Q: {p.get('question', '')[:100]}")
                st.write(f"A: {p.get('answer', '')[:100]}")
                st.markdown("---")

    st.markdown("### Preview (first 10 pairs)")
    for i, p in enumerate(valid[:10], 1):
        with st.expander(f"Q{i}: {p['question'][:80]}"):
            st.write(p["answer"])

    st.markdown("---")
    if st.button("✅ Add to Knowledge Base", type="primary", disabled=len(valid) == 0):
        with st.spinner("Embedding and uploading to Qdrant..."):
            try:
                count = embed_and_upsert(valid)
                # Archive the file
                os.makedirs(PAST_RFPS_DIR, exist_ok=True)
                shutil.copy(st.session_state["ingest_tmp_path"],
                            os.path.join(PAST_RFPS_DIR, source))
                log_ingest(source, len(valid) + len(skipped), count, len(skipped))
                st.success(f"🧠 Added {count} pairs to the knowledge base.")
                # Clear state
                for k in ["ingest_valid", "ingest_skipped", "ingest_source", "ingest_tmp_path"]:
                    st.session_state.pop(k, None)
            except Exception as e:
                st.error(f"Upload failed: {e}")
