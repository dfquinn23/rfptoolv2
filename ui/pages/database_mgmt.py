# ui/pages/database_mgmt.py
# Page 4: Database status, stats, and rebuild controls.

import streamlit as st
from core.qdrant_client import get_client, collection_stats, ensure_collections
from core.config import COLLECTION_RFP_ANSWERS, COLLECTION_COMPANY_DOCS, PAST_RFPS_DIR
import os
from pathlib import Path
from ingest.extractor import extract_qa_pairs
from ingest.validator import validate_pairs
from ingest.embedder import embed_and_upsert


def render():
    st.header("🔧 Database Management")

    # Connection status
    try:
        client = get_client()
        st.success("✅ Connected to Qdrant")
        stats = collection_stats()
        col1, col2 = st.columns(2)
        col1.metric("RFP Answers", stats.get(COLLECTION_RFP_ANSWERS, 0), help="Past Q&A pairs")
        col2.metric("Company Docs", stats.get(COLLECTION_COMPANY_DOCS, 0), help="Chunks from firm documents")
    except Exception as e:
        st.error(f"❌ Cannot connect to Qdrant: {e}")
        st.stop()

    st.markdown("---")

    # Rebuild RFP answers collection
    st.subheader("🔄 Rebuild RFP Answers Collection")
    st.write("Deletes all existing RFP answer vectors and re-ingests from archived files.")
    st.warning("⚠️ This will delete all existing RFP answer vectors. Archived files in `data/past_rfps/` will be reprocessed.")

    rfp_files = list(Path(PAST_RFPS_DIR).glob("*.docx")) if os.path.exists(PAST_RFPS_DIR) else []
    st.info(f"Found {len(rfp_files)} file(s) in `data/past_rfps/`")

    if rfp_files:
        confirm = st.checkbox("I understand this will delete and rebuild the RFP answers database")
        if st.button("Rebuild RFP Answers", type="primary", disabled=not confirm):
            with st.spinner("Rebuilding..."):
                try:
                    # Recreate collection
                    client.delete_collection(COLLECTION_RFP_ANSWERS)
                    ensure_collections()
                    st.success("✅ Collection recreated")

                    progress = st.progress(0)
                    total_pairs = 0
                    for i, path in enumerate(rfp_files):
                        st.write(f"Processing: {path.name}")
                        pairs = extract_qa_pairs(str(path))
                        valid, _ = validate_pairs(pairs)
                        n = embed_and_upsert(valid)
                        total_pairs += n
                        progress.progress((i + 1) / len(rfp_files))

                    st.success(f"🎉 Rebuild complete — {total_pairs} pairs ingested from {len(rfp_files)} files.")
                except Exception as e:
                    st.error(f"Rebuild failed: {e}")
