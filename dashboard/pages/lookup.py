import pandas as pd
import streamlit as st

from dashboard.queries import admission_score, request_versions
from dashboard.ui import current_publication, probability, to_new_york

st.title("Request lookup")
status = current_publication()
data_as_of = status["data_as_of"]
st.caption(
    "One Service Request as the current Publication saw it (data as of "
    f"{to_new_york(data_as_of):%b %d, %H:%M} New York time): selected fields, how fresh its Observation is, and "
    "where its Admission Score came from. Addresses and locations are not shown."
)

key = st.text_input("Request ID", placeholder="e.g. 66012345").strip()
if not key:
    st.stop()
versions = request_versions(key, data_as_of)
if versions.empty:
    st.info(f"No Request {key} in the current Publication's data.")
    st.stop()


def local_time(timestamp) -> str:
    return "–" if pd.isna(timestamp) else f"{to_new_york(timestamp):%b %d, %Y %H:%M}"


def source_time(value) -> str:
    """A source date: New York local, shown as recorded."""
    return "–" if pd.isna(value) or not value else pd.Timestamp(value).strftime("%b %d, %Y %H:%M")


latest = versions.iloc[0]
st.subheader("Fields")
st.dataframe(
    pd.DataFrame({
        "Field": ["Problem", "Problem Detail", "Agency", "Borough", "Channel", "Status", "Created", "Closed"],
        "Value": [
            latest["problem"], latest["problem_detail"], latest["agency"], latest["borough"], latest["channel"],
            latest["status"], source_time(latest["created_date"]), source_time(latest["closed_date"]),
        ],
    }).fillna("–"),
    hide_index=True,
    use_container_width=True,
)

st.subheader("Observation freshness")
last_seen = min(pd.Timestamp(latest["last_seen_extracted_at"]), pd.Timestamp(data_as_of))
c1, c2, c3 = st.columns(3)
c1.metric("Last observed", local_time(last_seen), help="Latest extraction that saw this state, up to the Publication's data time.")
c2.metric("Source last updated", local_time(latest["source_updated_at"]))
score = admission_score(key, status["publication_id"])
# A scored Request shows the First Observed frozen when it was scored.
first_observed = versions["first_extracted_at"].min() if score.empty else score.iloc[0]["first_observed_at"]
c3.metric("First Observed", local_time(first_observed))
st.caption(f"{len(versions)} Request Version(s) seen. Times are New York time.")

st.subheader("Admission Score")
if score.empty:
    if latest["agency"] == "NYPD":
        st.info("No Admission Score: NYPD Requests are outside the Modeling Cohort (ADR 0001).")
    else:
        st.info(
            "No Admission Score. A Request is scored once, by the first Publication that sees it, and only if it was "
            "First Observed within 72 hours of creation, is below its Seven-Day Horizon and a model is approved then. "
            "No score is filled in later."
        )
    st.stop()
s = score.iloc[0]
c1, c2, c3 = st.columns(3)
c1.metric("Admission Score", probability(s["admission_score"]), help="Predicted probability of being Delayed.")
c2.metric("Model version", s["model_version"])
c3.metric("Feature version", s["feature_version"])
st.caption(
    f"Scored {local_time(s['scored_at'])} in Publication {s['publication_id']}, from features frozen at the prediction "
    f"cutoff {local_time(pd.Timestamp(s['prediction_cutoff']).tz_localize('UTC'))} (First Observed "
    f"{local_time(s['first_observed_at'])}). Computed once; never recomputed."
)
st.dataframe(
    pd.DataFrame({
        "Input": ["Problem", "Agency", "Borough", "Channel", "Created hour", "Created weekday (1 = Monday)", "Created month"],
        "Value": [s["problem"], s["agency"], s["borough"], s["channel"], s["created_hour"], s["created_weekday"], s["created_month"]],
    }).astype(str),
    hide_index=True,
    use_container_width=True,
)
