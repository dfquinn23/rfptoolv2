"""
app.py — RFP Automation Tool (rfptoolv2)
Main entry point. Defines navigation across all pages.
"""

import streamlit as st

st.set_page_config(
    page_title="RFP Automation Tool",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

pipeline_page  = st.Page("pages/1_Pipeline.py",  title="Run Pipeline",  icon="⚡")
review_page    = st.Page("pages/2_Review.py",    title="Review & Export", icon="🔍")
database_page  = st.Page("pages/3_Database.py",  title="Database",      icon="🗄️")

nav = st.navigation([pipeline_page, review_page, database_page])
nav.run()
