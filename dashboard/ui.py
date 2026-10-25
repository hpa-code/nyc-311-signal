"""Pieces every page shares: the Publication it reads, filters, colors and
the durations table."""

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.queries import GroupFilter, publication_status

NEW_YORK = "America/New_York"
ALL = "All"
# Smallest group a geographic cell or borough comparison shows (instruction.md §12).
MIN_REQUESTS = 20
UNSPECIFIED_NOTE = "Requests missing a Problem, agency or borough are kept and shown as Unspecified."

# Validated default palette (dataviz skill), per theme: categorical slots 1-2,
# the blue sequential ramp (steps 100-700), and the page surface, which bar
# outlines use so adjacent marks read as a 2px gap. The map ramp runs from
# the surface toward contrast: light to dark on a light map, reversed on dark.
_SEQUENTIAL = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")
_LIGHT = {
    "blue": "#2a78d6",
    "orange": "#eb6834",
    "map_ramp": _SEQUENTIAL,
    "map_ramp_label": "Light to dark blue",
    "surface": "#ffffff",
}
_DARK = {
    "blue": "#3987e5",
    "orange": "#d95926",
    "map_ramp": _SEQUENTIAL[::-1],
    "map_ramp_label": "Dark to light blue",
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


def to_new_york(timestamp) -> pd.Timestamp:
    return pd.Timestamp(timestamp).tz_convert(NEW_YORK)


def group_filter(groups: pd.DataFrame, key: str) -> GroupFilter:
    """Problem, agency and borough selectors in one row, most Requests first."""
    chosen = []
    for column, container in zip(GroupFilter._fields, st.columns(3)):
        options = groups.groupby(column)["requests"].sum().sort_values(ascending=False).index.tolist()
        value = container.selectbox(column.capitalize(), [ALL, *options], key=f"{key}-{column}")
        chosen.append(None if value == ALL else value)
    return GroupFilter(*chosen)


def percent(numerator, denominator) -> str:
    """numerator / denominator as a whole percentage, or a dash with no denominator."""
    return "–" if not denominator else f"{numerator / denominator:.0%}"


def probability(p) -> str:
    """An Admission Score as a percentage, never rounded to a certain 0% or 100%."""
    if p < 0.001:
        return "<0.1%"
    if p > 0.999:
        return ">99.9%"
    return f"{p:.1%}"


def horizontal_bars(data: pd.DataFrame, title: str) -> alt.Chart:
    """One-series horizontal bars with the palette's mark spec; encode on the result."""
    colors = palette()
    return alt.Chart(data, title=title).mark_bar(
        cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color=colors["blue"], stroke=colors["surface"], strokeWidth=2
    )


def durations_table(rows: pd.DataFrame, label: str, labels: pd.Series) -> pd.DataFrame:
    """Durations with their denominators, closure rate and open count, one row
    per APP.PUBLISHED_DURATION_STATS row, first column `label`."""
    return pd.DataFrame({
        label: labels,
        "Requests": rows["requests"],
        "Still open": rows["open_requests"],
        "Closed within 7 days": [percent(a, b) for a, b in zip(rows["closed_within_horizon"], rows["horizon_passed"])],
        "Past 7 days (n)": rows["horizon_passed"],
        "Median days": (rows["median_hours"] / 24).round(1),
        "p90 days": (rows["p90_hours"] / 24).round(1),
        "Closures measured (n)": rows["timed_closures"],
    })
