import pandas as pd
import streamlit as st

from dashboard.queries import monitor_signals, publication_history, quarantine_summary, recent_runs
from dashboard.ui import current_publication, percent, to_new_york
from ingestion.reconcile import SWEPT_DAYS

KINDS = {"cursor": "Daily change cursor", "reconcile": "Reconciliation Pass", "bulk": "Bulk (backfill, slice)"}

st.title("Pipeline Health")
st.caption(
    "Live operational state, re-read every few minutes: it shows failures even while the dashboard keeps an earlier "
    "Publication. Times are New York time. Error messages and failed-check detail stay in the restricted OPS schema."
)

status = current_publication()
signals = monitor_signals()


def ago(hours) -> str:
    if pd.isna(hours):
        return "–"
    return f"{hours:.1f} h ago" if hours < 48 else f"{hours / 24:.1f} days ago"


def shown(value, pattern: str) -> str:
    """`value` formatted with `pattern` (as str.format), or a dash when it is missing."""
    return "–" if pd.isna(value) else pattern.format(value)


def local_time(timestamp) -> str:
    return "–" if pd.isna(timestamp) else f"{to_new_york(timestamp):%b %d, %H:%M}"


st.subheader("Publication")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Current Publication", local_time(status["published_at"]), help=str(status["publication_id"]))
c2.metric("Latest attempt", str(status["latest_attempt_state"]).capitalize(), help=f"Started {local_time(status['latest_attempt_at'])}")
c3.metric("Data as of", local_time(status["data_as_of"]), help="Latest extraction in the current Publication.")
c4.metric(
    "Scoring coverage", percent(signals["newly_scored"], signals["newly_eligible"]),
    help=f"Newly eligible Requests the latest Publication scored: {signals['newly_scored']:,} of {signals['newly_eligible']:,}.",
)
if not status["stale"]:
    st.success("The current Publication is complete and fresh.", icon=":material/check_circle:")

st.subheader("Collection and processing")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Last processing", ago(signals["hours_since_processing"]),
          help="Last successful readiness check (Observations reconciled and processed).")
c2.metric("Last cursor run", ago(signals["hours_since_cursor_run"]), help=str(signals["cursor_run_id"]))
c3.metric("Source freshness", shown(signals["source_lag_hours"], "{:.0f} h"),
          help="How old the newest change in the latest cursor run was when it was extracted. A run can succeed with stale source data.")
c4.metric("Runs stuck over 6 h", f"{int(signals['stuck_runs']):,}")
if signals["processing_failing"]:
    st.error("The readiness check has failed since it last succeeded. Detail is in OPS.STEP_STATUS.", icon=":material/error:")

st.subheader("Reconciliation Pass")
c1, c2, c3 = st.columns(3)
c1.metric("Last pass", shown(signals["days_since_reconciliation"], "{:.1f} days ago"),
          help=shown(signals["reconcile_run_id"], "{}"))
c2.metric("Changes the cursor missed", shown(signals["missed_changes"], "{:,.0f}"),
          help="Requests whose change the latest reconciliation found although the cursor should have collected it. They are taken in.")
c3.metric("Quarantined, last 7 days", f"{int(signals['quarantined_rows_7d']):,}")
st.caption(
    f"Each week re-fetches the {SWEPT_DAYS} creation days before it plus one older month in turn, to catch changes whose "
    "`:updated_at` never moved, or that predate the cursor."
)

st.subheader("Recent collector runs")
runs = recent_runs()
st.dataframe(
    pd.DataFrame({
        "Kind": runs["kind"].map(KINDS),
        "Run": runs["run_id"],
        "State": runs["state"],
        "Seen": runs["first_seen_at"].map(local_time),
        "Extracted": runs["extracted_rows"],
        "Loaded": runs["loaded_rows"],
        "Distinct Requests": runs["distinct_requests"],
        "Requests with a new Version": runs["updated_requests"],
        "Quarantined": runs["quarantined_rows"],
        "Missed by cursor": runs["missed_changes"],
        "Collector min": (runs["collector_seconds"] / 60).round(1),
        "Source lag h": runs["source_lag_hours"].round(1),
    }),
    hide_index=True,
    use_container_width=True,
)
st.caption("Runs processed before monitoring began have no per-run counts.")

st.subheader("Publication attempts")
history = publication_history()
st.dataframe(
    pd.DataFrame({
        "Publication": history["publication_id"],
        "State": history["state"],
        "Started": history["started_at"].map(local_time),
        "Took (s)": [
            "–" if pd.isna(end) else f"{(pd.Timestamp(end) - pd.Timestamp(start)).total_seconds():.0f}"
            for start, end in zip(history["started_at"], history["finished_at"])
        ],
    }),
    hide_index=True,
    use_container_width=True,
)

st.subheader("Quarantined Observations by reason")
quarantined = quarantine_summary(status["publication_id"])
if quarantined.empty:
    st.caption("None in the current Publication.")
else:
    st.dataframe(quarantined.rename(columns={"reason": "Reason", "observations": "Observations"}), hide_index=True)
st.caption(f"Unknown-Problem rate among the latest Publication's scored Requests: "
           f"{shown(signals['unknown_problem_rate'], '{:.1%}')}.")
