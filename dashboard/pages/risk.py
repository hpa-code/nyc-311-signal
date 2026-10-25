import math

import pandas as pd
import streamlit as st

from dashboard.queries import risk_queue_count, risk_queue_groups, risk_queue_page
from dashboard.ui import current_publication, group_filter, probability, to_new_york

PAGE_SIZE = 50

st.title("Risk Queue")
status = current_publication()
pid = status["publication_id"]
st.caption(
    "Recent Modeling Cohort Requests (NYPD excluded) still open and below their Seven-Day Horizon, highest Admission "
    "Score first. The Admission Score is the predicted probability, computed once shortly after the Request was First "
    f"Observed, that it will be Delayed (not Closed within 168 hours). Data as of {to_new_york(status['data_as_of']):%b %d, %H:%M} "
    "New York time."
)
st.warning(
    "A predicted delay is not urgency, danger or deserved priority. Scores reflect how similar Requests were closed "
    "in the past, including reporting patterns and historical service differences, not what a Request needs.",
    icon=":material/info:",
)

queued = risk_queue_groups(pid)
if queued.empty:
    st.info("No Requests in the Risk Queue.")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Requests in the queue", f"{int(queued['requests'].sum()):,}")
c2.metric("With an Admission Score", f"{int(queued['scored'].sum()):,}")
c3.metric(
    "Without one", f"{int(queued['requests'].sum() - queued['scored'].sum()):,}",
    help="No model was approved while they were eligible. They are listed last; no score is made up for them.",
)

selected = group_filter(queued, "risk")
total = risk_queue_count(pid, selected)
if total == 0:
    st.info("No Requests in the Risk Queue match these filters.")
    st.stop()

pages = math.ceil(total / PAGE_SIZE)
page = st.number_input(f"Page (of {pages:,})", min_value=1, max_value=pages, value=1, step=1) - 1
rows = risk_queue_page(pid, selected, int(page), PAGE_SIZE)
st.caption(f"Highest score first · {total:,} matching · showing {page * PAGE_SIZE + 1:,}–{page * PAGE_SIZE + len(rows):,}")
st.dataframe(
    pd.DataFrame({
        "Request ID": rows["unique_key"],
        "Problem": rows["problem"],
        "Agency": rows["agency"],
        "Borough": rows["borough"],
        "Created": pd.to_datetime(rows["created_date"]).dt.strftime("%b %d, %Y"),
        "Admission Score": rows["admission_score"].map(lambda s: "No score" if pd.isna(s) else probability(s)),
    }),
    hide_index=True,
    use_container_width=True,
)
st.caption("Look up any Request ID on the Request lookup page for its Observations and score provenance.")
