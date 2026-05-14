"""
pages/3_Database.py — Database Management

Upload completed RFPs to the answer library, view collection stats,
and rebuild the database when needed.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import tempfile

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
        url     = st.secrets.get("QDRANT_CLUSTER_URL", os.getenv("QDRANT_CLUSTER_URL"))
        api_key = st.secrets.get("QDRANT_API_KEY",     os.getenv("QDRANT_API_KEY"))
        return QdrantClient(url=url, api_key=api_key)
    except Exception as e:
        st.error(f"Qdrant connection failed: {e}")
        return None


def get_collection_name() -> str:
    return st.secrets.get("COLLECTION_NAME", os.getenv("COLLECTION_NAME", "past_rfp_answers"))


# ---------------------------------------------------------------------------
# Page
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
    st.warning(f"Collection **{collection_name}** does not exist yet. Upload documents or rebuild to create it.")
    point_count = 0
else:
    info        = client.get_collection(collection_name)
    point_count = info.points_count
    vec_size    = info.config.params.vectors.size

    col1, col2, col3 = st.columns(3)
    col1.metric("Collection",    collection_name)
    col2.metric("Answers stored", f"{point_count:,}")
    col3.metric("Vector dimensions", vec_size)
    st.success("Connected to Qdrant ✅")

st.divider()

# ---------------------------------------------------------------------------
# Upload completed RFP
# ---------------------------------------------------------------------------

st.subheader("Add Completed RFP to Library")
st.markdown(
    "Upload a **completed, answered** RFP document. The tool will extract Q&A pairs "
    "and add them to the answer library for future matching."
)

uploaded = st.file_uploader(
    "Upload completed RFP (.docx)",
    type=["docx"],
    help="This should be a fully answered RFP — not a blank incoming one.",
    key="db_upload",
)

if uploaded:
    st.info(f"**{uploaded.name}** ready to ingest.")

    if st.button("📥 Ingest into Library", type="primary", use_container_width=True):

        # Write to a temp file named after the original so source metadata is correct
        tmp_dir  = Path(tempfile.mkdtemp())
        tmp_path = tmp_dir / uploaded.name
        tmp_path.write_bytes(uploaded.read())

        with st.status("Ingesting document...", expanded=True) as status:
            try:
                from ingest.embedder import ingest_file
                st.write("🔍 Extracting Q&A pairs and embedding...")
                result = ingest_file(str(tmp_path), verbose=False)
                st.write(f"✅ {result['embedded']} pair(s) embedded. {result['skipped']} skipped.")
            except Exception as e:
                status.update(label="Ingestion failed.", state="error")
                st.error(f"Ingestion error: {e}")
                st.stop()

            status.update(label="Ingestion complete.", state="complete")

        st.success(
            f"**{uploaded.name}** ingested successfully. "
            f"{result['embedded']} new answer(s) added to the library."
        )
        st.cache_resource.clear()
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Rebuild database
# ---------------------------------------------------------------------------

st.subheader("Rebuild Database")
st.markdown(
    "Wipe and rebuild the entire collection from a folder of completed RFP documents. "
    "Use this if the database is corrupted or you want a clean start."
)

rebuild_dir = st.text_input(
    "Path to folder containing completed RFP documents",
    value="past_rfps",
    help="Absolute or relative path to a directory of answered .docx files.",
)

with st.expander("⚠️ Warning — this will delete all existing data", expanded=False):
    st.warning(
        "Rebuilding the database deletes all current vectors and re-embeds from scratch. "
        "This cannot be undone. Make sure all source documents are in the folder above "
        "before proceeding."
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
                            results     = ingest_directory(str(folder))
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
