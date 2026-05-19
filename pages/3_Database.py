"""
pages/3_Database.py — Database Management

Two-stage flow for adding completed RFPs to the answer library:
  Stage 1 — Upload → extract Q&A pairs → show questions for review (delete bad ones)
  Stage 2 — User enters document date → confirm → ingest with date stamp

Also shows collection stats and provides a full rebuild option.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import tempfile
from datetime import date as date_type

import streamlit as st
from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()

# ---------------------------------------------------------------------------
# Qdrant connection (cached)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_client() -> QdrantClient | None:
    try:
        try:
            url     = st.secrets["QDRANT_CLUSTER_URL"]
            api_key = st.secrets["QDRANT_API_KEY"]
        except Exception:
            url     = os.getenv("QDRANT_URL")
            api_key = os.getenv("QDRANT_API_KEY")
        return QdrantClient(url=url, api_key=api_key)
    except Exception as e:
        st.error(f"Qdrant connection failed: {e}")
        return None


def get_collection_name() -> str:
    try:
        return st.secrets["COLLECTION_NAME"]
    except Exception:
        return os.getenv("COLLECTION_NAME", "past_rfp_answers")


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.title("🗄️ Database")
st.markdown(
    "Upload completed RFPs to grow the answer library, view collection stats, "
    "or rebuild the database from scratch."
)
st.divider()

client          = get_client()
collection_name = get_collection_name()

# ---------------------------------------------------------------------------
# Connection status + stats
# ---------------------------------------------------------------------------

st.subheader("Collection Status")

if client is None:
    st.error("Cannot connect to Qdrant. Check your API key and cluster URL in secrets.")
    st.stop()

try:
    exists = client.collection_exists(collection_name)
except Exception as e:
    st.error(f"Error checking collection: {e}")
    st.stop()

if not exists:
    st.warning(f"Collection **{collection_name}** does not exist yet.")
    point_count = 0
else:
    info        = client.get_collection(collection_name)
    point_count = info.points_count
    vec_size    = info.config.params.vectors.size
    col1, col2, col3 = st.columns(3)
    col1.metric("Collection",        collection_name)
    col2.metric("Answers stored",    f"{point_count:,}")
    col3.metric("Vector dimensions", vec_size)
    st.success("Connected to Qdrant ✅")

st.divider()

# ---------------------------------------------------------------------------
# Ingest section — stage tracking
# ---------------------------------------------------------------------------

if "db_stage" not in st.session_state:
    st.session_state["db_stage"]    = "upload"
    st.session_state["db_pairs"]    = []
    st.session_state["db_source"]   = ""

# ---------------------------------------------------------------------------
# STAGE 1 — Upload & extract
# ---------------------------------------------------------------------------

st.subheader("Add Completed RFP to Library")

if st.session_state["db_stage"] == "upload":

    st.markdown(
        "Upload a **completed, answered** RFP document. The tool will extract Q&A pairs "
        "for your review before adding them to the library."
    )

    uploaded = st.file_uploader(
        "Upload completed RFP (.docx)",
        type=["docx"],
        help="Fully answered RFP — not a blank incoming one.",
        key="db_upload",
    )

    if uploaded:
        st.info(f"**{uploaded.name}** ready.")

        if st.button("🔍 Extract Q&A Pairs", type="primary", use_container_width=True):

            tmp_dir  = Path(tempfile.mkdtemp())
            tmp_path = tmp_dir / uploaded.name
            tmp_path.write_bytes(uploaded.read())

            with st.spinner("Extracting Q&A pairs..."):
                try:
                    from ingest.extractor import extract_qa_pairs
                    pairs = extract_qa_pairs(str(tmp_path))
                except Exception as e:
                    st.error(f"Extraction failed: {e}")
                    st.stop()

            if not pairs:
                st.warning("No Q&A pairs found. Check the document format.")
                st.stop()

            st.session_state["db_pairs"]  = pairs
            st.session_state["db_source"] = uploaded.name
            st.session_state["db_stage"]  = "review"
            st.rerun()

# ---------------------------------------------------------------------------
# STAGE 2 — Review questions & enter date
# ---------------------------------------------------------------------------

elif st.session_state["db_stage"] == "review":

    pairs  = st.session_state["db_pairs"]
    source = st.session_state["db_source"]

    st.markdown(
        f"**{len(pairs)}** Q&A pair(s) extracted from **{source}**.  \n"
        "Remove any pairs that were incorrectly extracted, enter the document date, "
        "then confirm to add to the library."
    )
    st.divider()

    # Question review list
    to_delete = []
    for i, pair in enumerate(pairs):
        col_num, col_q, col_btn = st.columns([0.5, 10, 1])
        with col_num:
            st.markdown(f"**{i + 1}.**")
        with col_q:
            st.markdown(pair.get("question", "*(no question)*"))
        with col_btn:
            if st.button("🗑️", key=f"db_del_{i}", help="Remove this pair"):
                to_delete.append(i)

    if to_delete:
        for idx in sorted(to_delete, reverse=True):
            pairs.pop(idx)
        st.session_state["db_pairs"] = pairs
        st.rerun()

    st.divider()

    remaining = len(st.session_state["db_pairs"])
    st.caption(f"{remaining} pair(s) remaining.")

    # Date input
    st.markdown("**Document date**")
    st.caption(
        "Enter the date of this RFP (e.g. when it was completed or submitted). "
        "This is used to prefer more recent answers when close matches are found."
    )
    doc_date = st.date_input(
        "Document date",
        value=date_type.today(),
        label_visibility="collapsed",
    )

    col_back, col_confirm = st.columns([1, 3])

    with col_back:
        if st.button("← Start Over", use_container_width=True):
            st.session_state["db_stage"]  = "upload"
            st.session_state["db_pairs"]  = []
            st.session_state["db_source"] = ""
            st.rerun()

    with col_confirm:
        if st.button(
            f"📥 Confirm & Ingest ({remaining} pairs)",
            type="primary",
            use_container_width=True,
            disabled=remaining == 0,
        ):
            pairs  = st.session_state["db_pairs"]
            source = st.session_state["db_source"]
            date_str = str(doc_date)   # "YYYY-MM-DD"

            with st.status("Ingesting...", expanded=True) as status:
                try:
                    from ingest.embedder import ingest_pairs
                    st.write("🧠 Embedding and uploading to Qdrant...")
                    result = ingest_pairs(pairs, source=source, date=date_str, verbose=False)
                    status.update(label="Ingestion complete.", state="complete")
                except Exception as e:
                    status.update(label="Ingestion failed.", state="error")
                    st.error(f"Ingestion error: {e}")
                    st.stop()

            st.success(
                f"**{source}** ingested successfully.  \n"
                f"{result['embedded']} answer(s) added · {result['skipped']} skipped · "
                f"Date: {date_str}"
            )

            # Reset stage
            st.session_state["db_stage"]  = "upload"
            st.session_state["db_pairs"]  = []
            st.session_state["db_source"] = ""
            st.cache_resource.clear()
            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Rebuild database
# ---------------------------------------------------------------------------

st.subheader("Rebuild Database")
st.markdown(
    "Wipe and rebuild the entire collection from a folder of completed RFP documents. "
    "Note: documents ingested this way will not have date stamps unless you add them manually afterwards."
)

rebuild_dir = st.text_input(
    "Path to folder containing completed RFP documents",
    value="past_rfps",
)

with st.expander("⚠️ Warning — this will delete all existing data", expanded=False):
    st.warning(
        "Rebuilding deletes all current vectors and re-embeds from scratch. "
        "This cannot be undone."
    )
    confirm = st.checkbox("I understand — proceed with rebuild")

    if confirm:
        if st.button("🔄 Rebuild Now", type="secondary", use_container_width=True):
            folder = Path(rebuild_dir)
            if not folder.exists() or not folder.is_dir():
                st.error(f"Folder not found: {rebuild_dir}")
            else:
                docs = list(folder.glob("*.docx"))
                if not docs:
                    st.error("No .docx files found in that folder.")
                else:
                    with st.status(f"Rebuilding from {len(docs)} document(s)...", expanded=True) as status:
                        try:
                            from ingest.embedder import ingest_directory
                            results       = ingest_directory(str(folder))
                            total_added   = sum(r.get("embedded", 0) for r in results)
                            total_skipped = sum(r.get("skipped",  0) for r in results)
                            status.update(label="Rebuild complete.", state="complete")
                            st.success(
                                f"Rebuild complete. {total_added} answer(s) indexed "
                                f"from {len(docs)} document(s). {total_skipped} skipped."
                            )
                            st.cache_resource.clear()
                            st.rerun()
                        except Exception as e:
                            status.update(label="Rebuild failed.", state="error")
                            st.error(f"Rebuild failed: {e}")
