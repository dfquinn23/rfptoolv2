# ui/pages/company_docs.py
# Page 3: Manage the company documents collection (ADV, decks, policies, etc.)

import streamlit as st
import os
import shutil
from tempfile import NamedTemporaryFile
from ingest.doc_ingest import ingest_company_doc
from core.config import COMPANY_DOCS_DIR
from core.qdrant_client import collection_stats
from core.config import COLLECTION_COMPANY_DOCS


def render():
    st.header("🏢 Company Documents")
    st.write(
        "Upload firm documents used as a fallback for novel RFP questions. "
        "These are searched when no good match exists in past RFP answers."
    )
    st.info("Good candidates: Form ADV, pitch decks, factsheets, compliance policies, org charts/bios.")

    # Show current doc count
    try:
        stats = collection_stats()
        count = stats.get(COLLECTION_COMPANY_DOCS, 0)
        st.metric("Chunks in Company Docs collection", count)
    except Exception:
        st.caption("Unable to retrieve stats — check database connection.")

    uploaded = st.file_uploader("Upload .docx company document", type="docx", key="company_doc")
    if not uploaded:
        return

    if st.button("Ingest Document", type="primary"):
        with NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            tmp.write(uploaded.getbuffer())
            tmp_path = tmp.name

        with st.spinner(f"Chunking and embedding {uploaded.name}..."):
            try:
                chunk_count = ingest_company_doc(tmp_path)
                os.makedirs(COMPANY_DOCS_DIR, exist_ok=True)
                shutil.copy(tmp_path, os.path.join(COMPANY_DOCS_DIR, uploaded.name))
                st.success(f"✅ Ingested {chunk_count} chunks from _{uploaded.name}_")
            except Exception as e:
                st.error(f"Ingestion failed: {e}")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
