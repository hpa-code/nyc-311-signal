"""NYC 311 Signal dashboard.

    uv run --env-file .env streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

import streamlit as st

# `streamlit run` puts dashboard/ on the path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

st.set_page_config(page_title="NYC 311 Signal", layout="wide")
page = st.navigation(
    [
        st.Page("pages/overview.py", title="Overview", default=True),
        st.Page("pages/geographic.py", title="Geographic patterns"),
        st.Page("pages/risk.py", title="Risk Queue"),
        st.Page("pages/overdue.py", title="Overdue Queue"),
        st.Page("pages/lookup.py", title="Request lookup"),
        st.Page("pages/model.py", title="Model Performance"),
        st.Page("pages/health.py", title="Pipeline Health"),
    ]
)
page.run()
