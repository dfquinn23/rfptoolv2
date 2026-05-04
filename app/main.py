# app/main.py
# Streamlit entry point. Run with: streamlit run app/main.py

import streamlit as st
import os
import sys

# Make project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.qdrant_client import ensure_collections
from ui.pages import process_rfp, ingest_history, company_docs, database_mgmt

st.set_page_config(
    page_title="RFP Assistant",
    page_icon="📄",
    layout="centered",
)

# Ensure data directories and Qdrant collections exist
for d in ["data/past_rfps", "data/company_docs", "data/output", "data/logs"]:
    os.makedirs(d, exist_ok=True)

try:
    ensure_collections()
except Exception as e:
    st.warning(f"⚠️ Database not connected: {e}. Configure secrets to enable full functionality.")

# Navigation
PAGES = {
    "📥 Process New RFP": process_rfp,
    "📚 Ingest History": ingest_history,
    "🏢 Company Docs": company_docs,
    "🔧 Database Management": database_mgmt,
}

st.sidebar.title("RFP Assistant")
selection = st.sidebar.radio("Navigate to", list(PAGES.keys()))
st.sidebar.markdown("---")
st.sidebar.caption("rfptoolv2 — built on OpenAI + Qdrant")

PAGES[selection].render()
