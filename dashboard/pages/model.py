import json

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.queries import label_summary, latest_model_version, model_evaluation, model_metrics
from dashboard.ui import current_publication, horizontal_bars, percent, to_new_york
from ml.examples import SPLITS

# Fewer Requests than this in a slice: its metrics are shown but flagged.
SMALL_SLICE = 100
SCORERS = {
    "constant_prevalence": "Constant prevalence (baseline)",
    "historical_problem": "Historical Problem rate (baseline)",
    "agency_only": "Agency-only rate (baseline)",
    "logistic_regression": "Logistic regression",
    "gradient_boosting": "Gradient boosting",
}
SLICES = {"agency": "Agency", "problem": "Problem", "borough": "Borough"}
REASONS = {
    "not_matured": "Not yet matured (horizon plus allowance not yet observed)",
    "reopened": "Reopened Request",
    "quarantined_observation": "Newer Observation quarantined",
    "closure_before_creation": "Closure before creation",
    "closed_without_closure_time": "Closed without a Closure time",
    "closure_time_while_open": "Closure time while open",
    "date_only_closure_on_horizon_day": "Date-Only Closure on the horizon day",
    "outside_modeling_cohort": "NYPD, outside the Modeling Cohort",
}

st.title("Model Performance")
st.caption(
    "How well a model predicts, from fields known at submission, that a Service Request will be Delayed: not Closed "
    "within 168 hours of creation. Measured on a later period it never saw, next to the baselines it has to beat. "
    "Training and evaluation are run by hand; this page shows the latest evaluated model version."
)

status = current_publication()
version = latest_model_version()
if version is None:
    st.info("No model evaluated yet.")
    st.stop()
evaluation = model_evaluation(version)
splits, selection, model = (json.loads(evaluation[c]) for c in ("splits", "selection", "model"))
metrics = model_metrics(version)
chosen = evaluation["chosen_model"]


def utc_day(iso: str) -> str:
    """A split boundary: split windows are UTC midnights."""
    return pd.Timestamp(iso).strftime("%b %d, %Y")


def number(value, digits: int = 3) -> str:
    return "–" if pd.isna(value) else f"{value:.{digits}f}"


def interval(row) -> str:
    if pd.isna(row["pr_auc_low"]):
        return number(row["pr_auc"])
    return f"{row['pr_auc']:.3f} ({row['pr_auc_low']:.3f}–{row['pr_auc_high']:.3f})"


def overall(split: str) -> pd.DataFrame:
    """A split's overall metrics, one row per scorer in SCORERS order."""
    rows = metrics[(metrics["split"] == split) & (metrics["slice_kind"] == "overall")].set_index("scorer")
    return rows.loc[[s for s in SCORERS if s in rows.index]].reset_index()


def comparison(rows: pd.DataFrame, with_interval: bool) -> pd.DataFrame:
    return pd.DataFrame({
        "Scorer": rows["scorer"].map(SCORERS),
        "Requests": rows["requests"],
        "Delayed": [percent(d, n) for d, n in zip(rows["delayed"], rows["requests"])],
        "PR-AUC (95% interval)" if with_interval else "PR-AUC": [interval(r) for _, r in rows.iterrows()]
        if with_interval else rows["pr_auc"].map(number),
        "Precision, top 10%": rows["top10_precision"].map(number),
        "Recall, top 10%": rows["top10_recall"].map(number),
        "Lift, top 10%": rows["top10_lift"].map(lambda v: number(v, 2)),
        "Precision at threshold": rows["precision"].map(number),
        "Recall at threshold": rows["recall"].map(number),
        "Threshold": rows["threshold"].map(number),
        "Brier score": rows["brier"].map(lambda v: number(v, 4)),
    })


st.markdown(f"Model version `{version}`")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Chosen model", SCORERS[chosen].removesuffix(" (baseline)"))
c2.metric("Feature version", evaluation["feature_version"])
c3.metric(
    "Label cutoff", to_new_york(pd.Timestamp(evaluation["label_cutoff"]).tz_localize("UTC")).strftime("%b %d, %Y"),
    help="Labels are the label versions known at this time (New York).",
)
c4.metric("Evaluated", to_new_york(evaluation["evaluated_at"]).strftime("%b %d, %Y"), help="New York time")
st.caption(f"Features: {', '.join(model['features'])} · scikit-learn {model['dependencies']['scikit-learn']}")

st.subheader("Test period")
test = overall("test")
t = splits["test"]
st.caption(
    f"Requests created {utc_day(t['window'][0])} to {utc_day(t['window'][1])} (UTC, end exclusive), scored once. Every scorer's threshold "
    "(highest F1) and the model's calibration were chosen on the validation period. The top 10% is the highest-scoring "
    "tenth, rounded up; Requests tied at its cutoff share the places left, so a constant score has lift 1. "
    "The interval is a 95% bootstrap interval over Requests."
)
st.dataframe(comparison(test, with_interval=True), hide_index=True, use_container_width=True)
lift = pd.DataFrame({"name": test["scorer"].map(SCORERS), "top10_lift": test["top10_lift"].astype(float)})
st.altair_chart(
    horizontal_bars(lift, "Lift in the top 10%, test period")
    .encode(
        x=alt.X("top10_lift:Q", title="Lift (Delayed share in the top 10% / prevalence)", axis=alt.Axis(grid=True, gridOpacity=0.3)),
        y=alt.Y("name:N", title=None, sort=None, axis=alt.Axis(labelLimit=320)),
        tooltip=[alt.Tooltip("name:N", title="Scorer"), alt.Tooltip("top10_lift:Q", title="Lift", format=".2f")],
    )
    .properties(height=alt.Step(36)),
    use_container_width=True,
)

st.subheader("Splits")
st.caption(
    "By creation time, with at least 240 hours (the Seven-Day Horizon plus the 72-hour reporting allowance) between "
    "windows. Each Request is one example, so all its Observations are in one split. Each split uses only Labels that "
    "had matured when it would have been used. Problems never seen in training are scored as Other, and counted here."
)
st.dataframe(
    pd.DataFrame([
        {
            "Split": name.capitalize(),
            "Created from (UTC)": utc_day(s["window"][0]),
            "Created before (UTC)": utc_day(s["window"][1]),
            "Labels matured by (UTC)": utc_day(s["labels_known_at"]),
            "Requests": s["requests"],
            "Delayed": percent(s["delayed"], s["requests"]),
            "Duplicate Closures, not Delayed": percent(s["duplicate_closures"]["not_delayed"], s["requests"] - s["delayed"]),
            "Duplicate Closures, Delayed": percent(s["duplicate_closures"]["delayed"], s["delayed"]),
            "Problems unseen in training (n)": s["unseen_problem_requests"],
            "Problems scored as Other (n)": s["other_problem_requests"],
        }
        for name, s in ((name, splits[name]) for name in SPLITS)
    ]),
    hide_index=True,
    use_container_width=True,
)

st.subheader("Choosing the model, validation period")
validation = overall("validation")
st.caption(
    f"Rule: {selection['rule'].replace('_', ' ')}. Chosen: {SCORERS[chosen]}. Logistic regression and gradient boosting "
    "are calibrated (Platt scaling) on this period, so their Brier scores here are optimistic."
)
st.dataframe(comparison(validation, with_interval=False), hide_index=True, use_container_width=True)

st.subheader("By agency, Problem and borough")
kind = st.segmented_control("Slice by", list(SLICES), format_func=lambda k: SLICES[k], default="agency") or "agency"
sliced = metrics[(metrics["split"] == "test") & (metrics["slice_kind"] == kind) & metrics["scorer"].isin([chosen, "agency_only"])]
wide = sliced.pivot(index="slice_value", columns="scorer")
model_rows = wide.xs(chosen, axis=1, level="scorer")
st.caption(
    f"Test period, {SCORERS[chosen]} beside the agency-only baseline. Precision, recall and lift in the top 10% are "
    f"within each group. Groups under {SMALL_SLICE} Requests are marked: their metrics are noisy."
)
st.dataframe(
    pd.DataFrame({
        SLICES[kind]: model_rows.index,
        "Requests": model_rows["requests"].astype(int).to_numpy(),
        "Few Requests": (model_rows["requests"] < SMALL_SLICE).map({True: "yes", False: ""}).to_numpy(),
        "Delayed": [percent(d, n) for d, n in zip(model_rows["delayed"], model_rows["requests"])],
        "PR-AUC, model": model_rows["pr_auc"].map(number).to_numpy(),
        "PR-AUC, agency-only": wide.xs("agency_only", axis=1, level="scorer")["pr_auc"].map(number).to_numpy(),
        "Lift top 10%, model": model_rows["top10_lift"].map(lambda v: number(v, 2)).to_numpy(),
        "Brier, model": model_rows["brier"].map(lambda v: number(v, 4)).to_numpy(),
    }).sort_values("Requests", ascending=False),
    hide_index=True,
    use_container_width=True,
)

st.subheader("Labels in the current Publication")
summary = label_summary(status["publication_id"])
labeled = summary[summary["label"].notna()]
st.caption(
    "Every Current Request, once. A Label is assigned only from an Observation at least 240 hours after creation. "
    "Duplicate Closures are Closures, labeled by the same timing rule (ADR 0003)."
)
d1, d2, d3 = st.columns(3)
d1.metric("Labeled Requests", f"{int(labeled['requests'].sum()):,}")
d2.metric("Delayed", percent(labeled.loc[labeled["label"] == 1, "requests"].sum(), labeled["requests"].sum()))
d3.metric("Duplicate Closures among labeled", percent(labeled["duplicate_closures"].sum(), labeled["requests"].sum()))
without = summary[summary["label"].isna()].groupby("no_label_reason", as_index=False)["requests"].sum()
st.dataframe(
    pd.DataFrame({
        "No Label because": without["no_label_reason"].map(lambda r: REASONS.get(r, r)),
        "Requests": without["requests"],
    }).sort_values("Requests", ascending=False),
    hide_index=True,
    use_container_width=True,
)

st.subheader("Limitations")
st.markdown(
    "- **NYPD is excluded** from training and evaluation (ADR 0001): it is about half of all Requests and almost never "
    "Delayed, so it would inflate PR-AUC and lift. Analytics and the Overdue Queue still include it.\n"
    "- **Retrospective, so approximate.** Features come from each Request's latest observed state; the source may have "
    "corrected its agency, Problem or borough after submission. Label versions are recorded only from October 24, 2026 "
    "on, so the earlier splits could not check which Labels were observable at the time, only that they had "
    "matured. A prospective evaluation on frozen first-observed inputs is the trustworthy measure.\n"
    "- **Date-Only Closures** (83% of DOB Closures) are judged by calendar day; one on the horizon day has no Label.\n"
    "- **The Label is administrative.** A Closure, including a Duplicate Closure, does not mean the problem was fixed, "
    "and seven days is a project threshold, not a city service-level agreement.\n"
    "- **Associations, not causes.** Reporting patterns and historical service differences shape the scores. A predicted "
    "delay is not urgency or deserved priority."
)
