import altair as alt
import pandas as pd
import streamlit as st

from dashboard.queries import data_coverage, overview_daily, publication_status

NEW_YORK = "America/New_York"
# Categorical slots 1-2 of the validated default palette, per theme; the bar
# outline matches the page surface so it reads as a 2px gap.
PALETTES = {
    "light": ({"Closed": "#2a78d6", "Open": "#eb6834"}, "#ffffff"),
    "dark": ({"Closed": "#3987e5", "Open": "#d95926"}, "#0e1117"),
}
SERIES, SURFACE = PALETTES["dark" if st.context.theme.type == "dark" else "light"]

st.title("Overview")
st.caption("NYC 311 Service Requests by creation date (New York time). Open means not yet Closed as of the latest Observation.")

status = publication_status()
if pd.isna(status["publication_id"]):
    st.info("No Publication yet.")
    st.stop()
if status["stale"]:
    st.warning(f"Stale data: {status['stale_reason']}. Showing the last valid Publication.", icon=":material/warning:")

daily = overview_daily(status["publication_id"])
coverage = data_coverage(status["publication_id"])

if daily.empty:
    st.info("No Requests loaded yet.")
    st.stop()

latest = pd.Timestamp(coverage["latest_extracted_at"]).tz_convert(NEW_YORK)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Requests", f"{int(daily['requests'].sum()):,}")
c2.metric("Manifest rows", f"{int(coverage['manifest_rows']):,}", help="Observations the collector's run manifests say were written.")
c3.metric("Distinct Requests observed", f"{int(coverage['distinct_requests_observed']):,}")
c4.metric("Latest extraction", latest.strftime("%b %d, %H:%M"), help="New York time")
st.caption(
    f"Coverage: created {coverage['first_created_date']:%b %d, %Y} to {coverage['last_created_date']:%b %d, %Y} · "
    f"{int(coverage['files_loaded'])} files from {int(coverage['runs'])} run(s) · "
    f"{int(coverage['observations_loaded']):,} Observations loaded · "
    f"Publication {status['publication_id']}, published "
    f"{pd.Timestamp(status['published_at']).tz_convert(NEW_YORK):%b %d, %H:%M} New York time"
)

long = daily.melt(
    id_vars=["created_date", "requests"],
    value_vars=["closed_requests", "open_requests"],
    var_name="state",
    value_name="count",
)
long["state"] = long["state"].map({"closed_requests": "Closed", "open_requests": "Open"})
long["created_date"] = pd.to_datetime(long["created_date"])

chart = (
    alt.Chart(long, title="Requests per day")
    .mark_bar(stroke=SURFACE, strokeWidth=2, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
    .encode(
        x=alt.X("yearmonthdate(created_date):O", title=None, axis=alt.Axis(format="%b %d", labelAngle=0)),
        y=alt.Y("count:Q", title="Requests", stack="zero", axis=alt.Axis(grid=True, gridOpacity=0.3)),
        color=alt.Color(
            "state:N",
            title=None,
            scale=alt.Scale(domain=list(SERIES), range=list(SERIES.values())),
            legend=alt.Legend(orient="top"),
        ),
        order=alt.Order("state:N", sort="ascending"),
        tooltip=[
            alt.Tooltip("yearmonthdate(created_date):T", title="Created", format="%a %b %d, %Y"),
            alt.Tooltip("state:N", title="State"),
            alt.Tooltip("count:Q", title="Requests", format=","),
            alt.Tooltip("requests:Q", title="Day total", format=","),
        ],
    )
    .properties(height=320)
)
st.altair_chart(chart, use_container_width=True)

with st.expander("Table"):
    st.dataframe(
        daily.rename(
            columns={
                "created_date": "Created",
                "requests": "Requests",
                "closed_requests": "Closed",
                "open_requests": "Open",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
