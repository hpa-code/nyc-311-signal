"""Pieces every page shares: the Publication it reads, filters and colors."""

import pandas as pd
import streamlit as st

from dashboard.queries import publication_status

NEW_YORK = "America/New_York"
ALL = "All"

# Validated default palette (dataviz skill), per theme: categorical slots 1-2,
# the blue sequential ramp (steps 100-700), and the page surface, which bar
# outlines use so adjacent marks read as a 2px gap.
SEQUENTIAL = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")
_LIGHT = {
    "series": ("#2a78d6", "#eb6834"),
    "sequential": SEQUENTIAL,
    "surface": "#ffffff",
}
_DARK = {
    "series": ("#3987e5", "#d95926"),
    "sequential": SEQUENTIAL,
    "surface": "#0e1117",
}


def palette() -> dict:
    return _DARK if st.context.theme.type == "dark" else _LIGHT


def current_publication() -> pd.Series:
    """The Publication this page view reads; stops the page if there is none,
    and warns if it is stale."""
    status = publication_status()
    if pd.isna(status["publication_id"]):
        st.info("No Publication yet.")
        st.stop()
    if status["stale"]:
        st.warning(f"Stale data: {status['stale_reason']}. Showing the last valid Publication.", icon=":material/warning:")
    return status


def new_york(timestamp) -> pd.Timestamp:
    return pd.Timestamp(timestamp).tz_convert(NEW_YORK)


def group_filters(groups: pd.DataFrame, key: str) -> tuple[str | None, str | None, str | None]:
    """Problem, agency and borough selectors in one row, most Requests first;
    None means all."""
    chosen = []
    for column, label, container in zip(("problem", "agency", "borough"), ("Problem", "Agency", "Borough"), st.columns(3)):
        options = groups.groupby(column)["requests"].sum().sort_values(ascending=False).index.tolist()
        value = container.selectbox(label, [ALL, *options], key=f"{key}-{column}")
        chosen.append(None if value == ALL else value)
    return chosen[0], chosen[1], chosen[2]


def share(numerator, denominator) -> str:
    return "–" if not denominator else f"{numerator / denominator:.0%}"
