import altair as alt
import pandas as pd
import pydeck as pdk  # type: ignore[import-untyped]
import streamlit as st

from dashboard.common import current_publication, palette, share
from dashboard.queries import borough_durations, geo_cells, groups

MIN_REQUESTS = 20
ALL_PROBLEMS = "All Problems"
colors = palette()

st.title("Geographic patterns")
st.caption(
    "Boroughs and map cells compared within one Problem, so differences are not just a different mix of Problems. "
    "Differences describe closure records, not the quality of service, and imply no cause."
)

pid = current_publication()["publication_id"]
problems = groups(pid).groupby("problem")["requests"].sum().sort_values(ascending=False).index.tolist()
choice = st.selectbox("Problem", [*problems, ALL_PROBLEMS], help="Most Requests first.")
problem = None if choice == ALL_PROBLEMS else choice
if problem is None:
    st.warning("All Problems mixes Problems that close at very different speeds; compare within one Problem first.", icon=":material/info:")

# Boroughs, within the Problem.
boroughs = borough_durations(pid, problem)
shown = boroughs[boroughs["requests"] >= MIN_REQUESTS].copy()
shown["rate"] = shown["closed_within_horizon"] / shown["horizon_passed"].where(shown["horizon_passed"] > 0)
st.subheader("By borough")
if shown.empty:
    st.info(f"No borough has {MIN_REQUESTS} or more Requests for this Problem.")
else:
    bars = shown.dropna(subset=["rate"])
    st.altair_chart(
        alt.Chart(bars, title="Closed within 7 days")
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color=colors["series"][0], stroke=colors["surface"], strokeWidth=2)
        .encode(
            x=alt.X("rate:Q", title="Share of Requests past their Seven-Day Horizon", axis=alt.Axis(format="%", grid=True, gridOpacity=0.3),
                    scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("borough:N", title=None, sort="-x"),
            tooltip=[
                alt.Tooltip("borough:N", title="Borough"),
                alt.Tooltip("rate:Q", title="Closed within 7 days", format=".0%"),
                alt.Tooltip("horizon_passed:Q", title="Past 7 days (n)", format=","),
                alt.Tooltip("open_requests:Q", title="Unresolved", format=","),
            ],
        )
        .properties(height=36 * len(bars) + 40),
        use_container_width=True,
    )
    st.dataframe(
        pd.DataFrame({
            "Borough": shown["borough"],
            "Requests": shown["requests"],
            "Unresolved": shown["open_requests"],
            "Closed within 7 days": [share(a, b) for a, b in zip(shown["closed_within_horizon"], shown["horizon_passed"])],
            "Past 7 days (n)": shown["horizon_passed"],
            "Median days": (shown["median_hours"] / 24).round(1),
            "p90 days": (shown["p90_hours"] / 24).round(1),
            "Closures measured (n)": shown["timed_closures"],
        }),
        hide_index=True,
        use_container_width=True,
    )
hidden = boroughs[boroughs["requests"] < MIN_REQUESTS]
if not hidden.empty:
    st.caption(f"{len(hidden)} borough group(s) with fewer than {MIN_REQUESTS} Requests hidden ({int(hidden['requests'].sum()):,} Requests).")

# Map of H3 cells (about 0.7 km² each), within the Problem.
st.subheader("Map")
cells = geo_cells(pid, problem)
located = cells[cells["cell_kind"] == "cell"].copy()
METRICS = {"Closed within 7 days": "rate", "Requests": "requests"}
metric = METRICS[st.radio("Color by", list(METRICS), horizontal=True)]
if located.empty:
    st.info(f"No map cell has {MIN_REQUESTS} or more Requests for this Problem.")
else:
    # A rate needs its own minimum: cells with fewer than 20 Requests past the horizon stay gray.
    located["rate"] = located["closed_within_horizon"] / located["horizon_passed"].where(located["horizon_passed"] >= MIN_REQUESTS)
    # The ramp spans the cells' own range; more is darker on a light map and
    # lighter on a dark one, so the extreme cells always stand out.
    ramp = colors["sequential"] if st.context.theme.type != "dark" else colors["sequential"][::-1]
    values = located[metric]
    lo, hi = float(values.min()), float(values.max())
    steps = ((values - lo) / ((hi - lo) or 1) * (len(ramp) - 1)).round()

    def rgb(hex_color: str) -> list[int]:
        return [int(hex_color[i : i + 2], 16) for i in (1, 3, 5)]

    located["fill"] = [rgb(ramp[int(s)]) + [200] if pd.notna(s) else [150, 150, 150, 120] for s in steps]
    located["label_rate"] = [share(a, b) if b >= MIN_REQUESTS else "too few to rate" for a, b in zip(located["closed_within_horizon"], located["horizon_passed"])]
    located["label_median"] = [f"{h / 24:.1f} days" if pd.notna(h) else "–" for h in located["median_hours"]]
    st.pydeck_chart(
        pdk.Deck(
            layers=[
                pdk.Layer(
                    "H3HexagonLayer",
                    located[["h3_cell", "fill", "requests", "open_requests", "label_rate", "label_median"]],
                    get_hexagon="h3_cell",
                    get_fill_color="fill",
                    get_line_color=rgb(colors["surface"]),
                    line_width_min_pixels=1,
                    pickable=True,
                    auto_highlight=True,
                )
            ],
            initial_view_state=pdk.ViewState(latitude=40.71, longitude=-73.94, zoom=9.8),
            tooltip={"html": "<b>{requests}</b> Requests · {open_requests} open<br/>Closed within 7 days: {label_rate}<br/>Median to Closure: {label_median}"},
        ),
        use_container_width=True,
    )
    legend = f"{lo:.0%} → {hi:.0%} closed within 7 days" if metric == "rate" else f"{int(lo):,} → {int(hi):,} Requests"
    direction = "Dark to light" if st.context.theme.type == "dark" else "Light to dark"
    st.caption(f"{direction} blue: {legend}. Gray: fewer than {MIN_REQUESTS} Requests past their Seven-Day Horizon.")

totals = cells.groupby("cell_kind")["requests"].sum()
st.caption(
    f"Only cells with at least {MIN_REQUESTS} Requests are drawn: {int(totals.get('hidden', 0)):,} Requests in smaller cells are hidden, "
    f"and {int(totals.get('no_location', 0)):,} have no location in NYC. No addresses or coordinates are published."
)
