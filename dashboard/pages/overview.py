import altair as alt
import pandas as pd
import streamlit as st

from dashboard.common import current_publication, group_filters, new_york, palette, share
from dashboard.queries import backlog, daily_stats, data_coverage, durations, groups, overview_daily

colors = palette()
FIRST, SECOND = colors["series"]

st.title("Overview")
st.caption("NYC 311 Service Requests, all agencies including NYPD. Dates are New York time.")

status = current_publication()
pid = status["publication_id"]
daily = overview_daily(pid)
coverage = data_coverage(pid)
if daily.empty:
    st.info("No Requests loaded yet.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Requests", f"{int(daily['requests'].sum()):,}")
c2.metric("Open now", f"{int(daily['open_requests'].sum()):,}", help="Not Closed as of the latest Observation.")
c3.metric("Distinct Requests observed", f"{int(coverage['distinct_requests_observed']):,}")
c4.metric("Latest extraction", new_york(coverage["latest_extracted_at"]).strftime("%b %d, %H:%M"), help="New York time")
st.caption(
    f"Coverage: created {coverage['first_created_date']:%b %d, %Y} to {coverage['last_created_date']:%b %d, %Y} · "
    f"{int(coverage['files_loaded'])} files from {int(coverage['runs'])} run(s) · "
    f"{int(coverage['observations_loaded']):,} Observations loaded · "
    f"{int(coverage['manifest_rows']):,} rows in run manifests · "
    f"Publication {pid}, published {new_york(status['published_at']):%b %d, %H:%M} New York time"
)

problem, agency, borough = group_filters(groups(pid), "overview")

# Submission and Closure trends, by week (Monday start).
stats = daily_stats(pid, problem, agency, borough)
if stats.empty:
    st.info("No Requests match these filters.")
    st.stop()
# Whole weeks only, within the loaded creation dates: partial edge weeks (and
# Closure dates the source records in the future) would read as drops.
dates = pd.to_datetime(stats["stat_date"])
first = pd.Timestamp(coverage["first_created_date"])
last = pd.Timestamp(coverage["last_created_date"])
stats["week"] = dates.dt.to_period("W-SUN").dt.start_time
weeks = stats[(stats["week"] >= first) & (stats["week"] + pd.Timedelta(days=6) <= last)]
weekly = weeks.groupby("week", as_index=False)[["submitted", "closed"]].sum()
trend = weekly.melt(id_vars="week", var_name="series", value_name="requests")
trend["series"] = trend["series"].map({"submitted": "Submitted", "closed": "Closed"})
st.altair_chart(
    alt.Chart(trend, title="Submissions and Closures per week")
    .mark_line(strokeWidth=2)
    .encode(
        x=alt.X("week:T", title=None, axis=alt.Axis(format="%b %Y")),
        y=alt.Y("requests:Q", title="Requests", axis=alt.Axis(grid=True, gridOpacity=0.3)),
        color=alt.Color(
            "series:N", title=None, scale=alt.Scale(domain=["Submitted", "Closed"], range=[FIRST, SECOND]),
            legend=alt.Legend(orient="top"),
        ),
        tooltip=[
            alt.Tooltip("week:T", title="Week of", format="%a %b %d, %Y"),
            alt.Tooltip("series:N", title="Series"),
            alt.Tooltip("requests:Q", title="Requests", format=","),
        ],
    )
    .properties(height=280),
    use_container_width=True,
)
st.caption(
    "Submitted counts Requests by creation date; Closed counts Closures by Closure date "
    "(Closures without a date are left out). Whole weeks only, Monday to Sunday."
)

# Backlog: observed snapshots beside the reconstruction.
KINDS = {"observed": "Observed", "retrospective": "Retrospective"}
open_backlog = backlog(pid, problem, agency, borough)
open_backlog["kind"] = open_backlog["kind"].map(KINDS)
open_backlog["backlog_date"] = pd.to_datetime(open_backlog["backlog_date"])
backlog_chart = (
    alt.Chart(open_backlog, title="Open backlog at the end of each day")
    .encode(
        x=alt.X("backlog_date:T", title=None, axis=alt.Axis(format="%b %Y")),
        y=alt.Y("open_requests:Q", title="Open Requests", axis=alt.Axis(grid=True, gridOpacity=0.3)),
        color=alt.Color(
            "kind:N", title=None, scale=alt.Scale(domain=list(KINDS.values()), range=[SECOND, FIRST]),
            legend=alt.Legend(orient="top"),
        ),
        strokeDash=alt.StrokeDash(
            "kind:N", scale=alt.Scale(domain=list(KINDS.values()), range=[[1, 0], [6, 4]]), legend=None
        ),
        tooltip=[
            alt.Tooltip("backlog_date:T", title="Day", format="%a %b %d, %Y"),
            alt.Tooltip("kind:N", title="Backlog"),
            alt.Tooltip("open_requests:Q", title="Open Requests", format=","),
        ],
    )
)
st.altair_chart(
    (backlog_chart.mark_line(strokeWidth=2) + backlog_chart.transform_filter(alt.datum.kind == KINDS["observed"]).mark_point(size=64, filled=True))
    .properties(height=280),
    use_container_width=True,
)
st.caption(
    "**Observed** is what the pipeline saw open on that day, saved once a day; it starts when snapshots began. "
    "**Retrospective** is rebuilt from today's data, so it knows Closures recorded later than the day itself, "
    "counts only Requests created since Jan 1, 2025 (early 2025 understates the real backlog), "
    "and leaves out Requests Closed without a usable Closure time."
)

# Durations with their denominators, closure rates and unresolved counts.
st.subheader("Time to Closure")
months = durations(pid, problem, agency, borough)
overall = months[months["created_month"].isna()].iloc[0]
c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Closed within 7 days", share(overall["closed_within_horizon"], overall["horizon_passed"]),
    help=f"Of {int(overall['horizon_passed']):,} Requests past their Seven-Day Horizon with a known outcome.",
)
c2.metric("Median to Closure", f"{overall['median_hours'] / 24:.1f} days" if pd.notna(overall["median_hours"]) else "–",
          help=f"Of {int(overall['timed_closures']):,} Closures with a usable Closure time.")
c3.metric("p90 to Closure", f"{overall['p90_hours'] / 24:.1f} days" if pd.notna(overall["p90_hours"]) else "–",
          help=f"Of {int(overall['timed_closures']):,} Closures with a usable Closure time.")
c4.metric("Unresolved", f"{int(overall['open_requests']):,}", help=f"Of {int(overall['requests']):,} Requests, still open.")

by_month = months[months["created_month"].notna()].copy()
by_month["created_month"] = pd.to_datetime(by_month["created_month"])
lines = by_month.melt(id_vars=["created_month", "timed_closures", "open_requests", "requests"], value_vars=["median_hours", "p90_hours"],
                      var_name="statistic", value_name="hours")
lines["days"] = lines["hours"] / 24
lines["statistic"] = lines["statistic"].map({"median_hours": "Median", "p90_hours": "p90"})
st.altair_chart(
    alt.Chart(lines, title="Days to Closure, by creation month (closed Requests only)")
    .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=64, filled=True))
    .encode(
        x=alt.X("yearmonth(created_month):T", title=None, axis=alt.Axis(format="%b %Y")),
        y=alt.Y("days:Q", title="Days", axis=alt.Axis(grid=True, gridOpacity=0.3)),
        color=alt.Color("statistic:N", title=None, scale=alt.Scale(domain=["Median", "p90"], range=[FIRST, SECOND]),
                        legend=alt.Legend(orient="top")),
        tooltip=[
            alt.Tooltip("yearmonth(created_month):T", title="Created", format="%B %Y"),
            alt.Tooltip("statistic:N", title="Statistic"),
            alt.Tooltip("days:Q", title="Days", format=".1f"),
            alt.Tooltip("timed_closures:Q", title="Closures measured", format=","),
            alt.Tooltip("open_requests:Q", title="Still open", format=","),
            alt.Tooltip("requests:Q", title="Requests", format=","),
        ],
    )
    .properties(height=280),
    use_container_width=True,
)
st.caption(
    "Durations count only Requests already Closed, so recent months look faster than they will: read them with "
    "the unresolved count and the 7-day closure rate below. Some agencies record Closure dates without a time "
    "(Date-Only Closures), which can shift a duration by up to a day."
)

table = pd.DataFrame({
    "Created": by_month["created_month"].dt.strftime("%b %Y"),
    "Requests": by_month["requests"],
    "Unresolved": by_month["open_requests"],
    "Closed within 7 days": [share(a, b) for a, b in zip(by_month["closed_within_horizon"], by_month["horizon_passed"])],
    "Past 7 days (n)": by_month["horizon_passed"],
    "Median days": (by_month["median_hours"] / 24).round(1),
    "p90 days": (by_month["p90_hours"] / 24).round(1),
    "Closures measured (n)": by_month["timed_closures"],
})
st.dataframe(table.iloc[::-1], hide_index=True, use_container_width=True)
