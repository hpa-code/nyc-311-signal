import math

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.queries import data_coverage, overdue_count, overdue_groups, overdue_page
from dashboard.ui import current_publication, group_filter, horizontal_bars, to_new_york

PAGE_SIZE = 50

st.title("Overdue Queue")
pid = current_publication()["publication_id"]
as_of = to_new_york(data_coverage(pid)["latest_extracted_at"])
st.caption(
    f"Requests observed still open past their Seven-Day Horizon (more than 168 hours after creation), as of the latest extraction, "
    f"{as_of:%b %d, %H:%M} New York time. Every agency is included, NYPD too. This is an observed fact, "
    "not a prediction: it is separate from any Admission Score."
)

overdue = overdue_groups(pid)
if overdue.empty:
    st.info("No Overdue Requests.")
    st.stop()

st.metric("Overdue Requests", f"{int(overdue['requests'].sum()):,}")
by_agency = overdue.groupby("agency")["requests"].sum().nlargest(10).reset_index()
st.altair_chart(
    horizontal_bars(by_agency, "Overdue Requests by agency (top 10)")
    .encode(
        x=alt.X("requests:Q", title="Overdue Requests", axis=alt.Axis(grid=True, gridOpacity=0.3)),
        y=alt.Y("agency:N", title=None, sort="-x"),
        tooltip=[alt.Tooltip("agency:N", title="Agency"), alt.Tooltip("requests:Q", title="Overdue", format=",")],
    )
    .properties(height=32 * len(by_agency) + 40),
    use_container_width=True,
)

selected = group_filter(overdue, "overdue")
total = overdue_count(pid, selected)
if total == 0:
    st.info("No Overdue Requests match these filters.")
    st.stop()

pages = math.ceil(total / PAGE_SIZE)
page = st.number_input(f"Page (of {pages:,})", min_value=1, max_value=pages, value=1, step=1) - 1
rows = overdue_page(pid, selected, int(page), PAGE_SIZE)
st.caption(f"Oldest first · {total:,} matching · showing {page * PAGE_SIZE + 1:,}–{page * PAGE_SIZE + len(rows):,}")
st.dataframe(
    pd.DataFrame({
        "Request ID": rows["unique_key"],
        "Problem": rows["problem"],
        "Agency": rows["agency"],
        "Borough": rows["borough"],
        "Status": rows["status"],
        "Created (New York)": pd.to_datetime(rows["created_at_local"]).dt.strftime("%b %d, %Y %H:%M"),
        "Days open": rows["hours_open"] // 24,
    }),
    hide_index=True,
    use_container_width=True,
)
