"""Train and evaluate a Delayed model against its baselines (instruction.md §11),
run by hand; it never runs in the daily pipeline.

    uv run --env-file .env python -m ml.train

One evaluation, in one transaction:
  1. Snapshot the examples (ml.examples) for a split plan and label cutoff.
  2. On training examples only: fit the category levels (unseen and rare
     levels read as OTHER), the three baselines (constant prevalence, the
     training delay rate of the Request's Problem, of its agency) and two
     candidates, logistic regression and gradient boosting.
  3. On validation examples only: calibrate each candidate (Platt scaling),
     choose each scorer's threshold (highest F1), and choose the model:
     gradient boosting only if its PR-AUC is at least MATERIAL_GAIN higher.
  4. Score the test period once: the baselines and the chosen model, overall
     (with a bootstrap interval for PR-AUC) and by agency, Problem and borough.
  5. Record everything in ML.MODEL_EVALUATIONS and ML.MODEL_METRICS: this
     registers the version, with reference scores that promotion
     (ml.promote) checks the warehouse reproduces.
"""

import json
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import OneHotEncoder
from snowflake.connector import SnowflakeConnection

from ml.examples import GAP, SPLITS, SplitPlan, load_examples, snapshot_examples
from ml.features import FEATURE_VERSION, FEATURES, MIN_LEVEL_REQUESTS, OTHER, CategoryMap
from ml.metrics import best_f1_threshold, bootstrap_pr_auc, summarize
from warehouse.connection import connect
from warehouse.environment import current

BASELINES = ("constant_prevalence", "historical_problem", "agency_only")
CANDIDATES = ("logistic_regression", "gradient_boosting")
# The PR-AUC gain on validation that earns gradient boosting its complexity.
MATERIAL_GAIN = 0.02
SLICES = ("agency", "problem", "borough")
# Requests are created from 2025-01-01, New York time.
FIRST_CREATED = datetime(2025, 1, 1, 5)
WINDOW = timedelta(weeks=8)
LOGISTIC_C = 1.0
BOOSTING_PARAMS = {"max_iter": 200, "learning_rate": 0.1, "random_state": 0}
REFERENCE_REQUESTS = 20

Scorer = Callable[[pd.DataFrame], np.ndarray]


def default_plan(label_cutoff: datetime) -> SplitPlan:
    """Test: the latest 8 weeks of Requests whose Labels matured by the cutoff;
    validation: 8 weeks ending GAP before it; training: everything before that."""
    test_end = datetime(label_cutoff.year, label_cutoff.month, label_cutoff.day) - GAP
    test_start = test_end - WINDOW
    validation_end = test_start - GAP
    validation_start = validation_end - WINDOW
    return SplitPlan(
        train=(FIRST_CREATED, validation_start - GAP),
        validation=(validation_start, validation_end),
        test=(test_start, test_end),
    )


def evaluate(conn: SnowflakeConnection, plan: SplitPlan | None = None, label_cutoff: datetime | None = None) -> str:
    """Train and evaluate a new model version on Labels known at `label_cutoff`
    (default: the latest label version); returns the model version."""
    cur = conn.cursor()
    if label_cutoff is None:
        row = cur.execute("SELECT MAX(observed_through) FROM ML.TRAINING_LABELS").fetchone()
        assert isinstance(row, tuple) and row[0] is not None, "no label versions yet"
        label_cutoff = row[0]
    plan = plan or default_plan(label_cutoff)
    version = "model-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    cur.execute("BEGIN")
    try:
        snapshot_examples(conn, version, plan, label_cutoff)
        result = measure(load_examples(conn, version), plan, label_cutoff)
        _record(conn, version, label_cutoff, result)
        cur.execute("COMMIT")
    except BaseException:
        cur.execute("ROLLBACK")
        raise
    return version


@dataclass
class Result:
    splits: dict
    selection: dict
    model: dict
    metrics: list[dict]

    @property
    def chosen(self) -> str:
        return self.selection["chosen"]


def measure(examples: pd.DataFrame, plan: SplitPlan, label_cutoff: datetime) -> Result:
    by_split = {name: examples[examples["split"] == name].reset_index(drop=True) for name in SPLITS}
    train, validation, test = by_split["train"], by_split["validation"], by_split["test"]
    if train.empty or validation.empty or test.empty:
        raise ValueError(f"every split needs examples: {({k: len(v) for k, v in by_split.items()})}")
    categories = CategoryMap.fit(train)
    y_train, y_validation = train["label"].to_numpy(), validation["label"].to_numpy()

    prevalence = float(y_train.mean())
    scorers: dict[str, Scorer] = {
        "constant_prevalence": lambda frame: np.full(len(frame), prevalence),
        "historical_problem": _rate_baseline(train, categories, "problem", prevalence),
        "agency_only": _rate_baseline(train, categories, "agency", prevalence),
    }
    logistic, weights = _fit_logistic(train, categories)
    boosting = _fit_boosting(train, categories)
    calibrations = {}
    for name, raw in (("logistic_regression", logistic), ("gradient_boosting", boosting)):
        calibrations[name] = _platt(raw(validation), y_validation)
        scorers[name] = _calibrated(raw, calibrations[name])

    validation_scores = {name: scorer(validation) for name, scorer in scorers.items()}
    thresholds = {name: _threshold(y_validation, s) for name, s in validation_scores.items()}
    validation_metrics = {name: summarize(y_validation, s, thresholds[name]) for name, s in validation_scores.items()}
    gain = _gain(validation_metrics["gradient_boosting"]["pr_auc"], validation_metrics["logistic_regression"]["pr_auc"])
    chosen = "gradient_boosting" if gain is not None and gain >= MATERIAL_GAIN else "logistic_regression"

    metrics = [
        _row("validation", name, "overall", None, m, thresholds[name]) for name, m in validation_metrics.items()
    ]
    y_test = test["label"].to_numpy()
    for name in (*BASELINES, chosen):
        scores = scorers[name](test)
        low, high = bootstrap_pr_auc(y_test, scores)
        metrics.append(_row("test", name, "overall", None, summarize(y_test, scores, thresholds[name]), thresholds[name], low, high))
        for kind in SLICES:
            for value, rows in test.groupby(kind).groups.items():
                m = summarize(y_test[rows], scores[rows], thresholds[name])
                metrics.append(_row("test", name, kind, str(value), m, thresholds[name]))

    model = {
        "kind": chosen,
        "feature_version": FEATURE_VERSION,
        "features": list(FEATURES),
        "min_level_requests": MIN_LEVEL_REQUESTS,
        "other_level": OTHER,
        "levels": {f: list(v) for f, v in categories.levels.items()},
        "calibration": calibrations[chosen],
        "threshold": thresholds[chosen],
        "logistic_regression": {"C": LOGISTIC_C, **weights} if chosen == "logistic_regression" else None,
        "gradient_boosting": BOOSTING_PARAMS if chosen == "gradient_boosting" else None,
        "reference_scores": _reference_scores(test, scorers[chosen]),
        "dependencies": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }
    selection = {
        "chosen": chosen,
        "rule": f"gradient_boosting only if its validation PR-AUC exceeds logistic_regression's by at least {MATERIAL_GAIN}",
        "validation_pr_auc": {name: validation_metrics[name]["pr_auc"] for name in CANDIDATES},
        "gain": gain,
    }
    return Result(_split_summary(by_split, plan, label_cutoff, categories), selection, model, metrics)


def _reference_scores(test: pd.DataFrame, scorer: Scorer) -> list[dict]:
    """Features and scores of REFERENCE_REQUESTS test examples, which promotion
    checks the warehouse reproduces (ml.promote)."""
    sample = test.sort_values("unique_key").head(REFERENCE_REQUESTS)
    scores = scorer(sample)
    return [
        {**{f: (str(v) if isinstance(v, str) else int(v)) for f, v in row.items()}, "score": float(score)}
        for row, score in zip(sample[list(FEATURES)].to_dict("records"), scores)
    ]


def _rate_baseline(train: pd.DataFrame, categories: CategoryMap, feature: str, prevalence: float) -> Scorer:
    """The training delay rate of each level; OTHER takes the rate of the rare
    levels, or the prevalence if training had none."""
    rates = train["label"].groupby(categories.transform(train)[feature], observed=True).mean()

    def score(frame: pd.DataFrame) -> np.ndarray:
        levels = categories.transform(frame)[feature].astype(str)
        return levels.map(rates.to_dict()).fillna(prevalence).to_numpy(dtype=float)

    return score


def _fit_logistic(train: pd.DataFrame, categories: CategoryMap) -> tuple[Scorer, dict]:
    encoder = OneHotEncoder(categories=[[*categories.levels[f], OTHER] for f in FEATURES])
    model = LogisticRegression(C=LOGISTIC_C, max_iter=1000)
    model.fit(encoder.fit_transform(categories.transform(train).astype(str)), train["label"])

    def score(frame: pd.DataFrame) -> np.ndarray:
        return model.predict_proba(encoder.transform(categories.transform(frame).astype(str)))[:, 1]

    coefficients = iter(model.coef_[0])
    weights = {f: {level: float(next(coefficients)) for level in levels} for f, levels in zip(FEATURES, encoder.categories_)}
    return score, {"intercept": float(model.intercept_[0]), "weights": weights}


def _fit_boosting(train: pd.DataFrame, categories: CategoryMap) -> Scorer:
    model = HistGradientBoostingClassifier(categorical_features="from_dtype", **BOOSTING_PARAMS)
    model.fit(categories.transform(train), train["label"])
    return lambda frame: model.predict_proba(categories.transform(frame))[:, 1]


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _platt(raw: np.ndarray, labels: np.ndarray) -> dict:
    """Slope and intercept on the logit, fit on validation; identity if it has one class."""
    if labels.min() == labels.max():
        return {"slope": 1.0, "intercept": 0.0}
    fit = LogisticRegression(C=1e6).fit(_logit(raw).reshape(-1, 1), labels)
    return {"slope": float(fit.coef_[0][0]), "intercept": float(fit.intercept_[0])}


def _calibrated(raw: Scorer, calibration: dict) -> Scorer:
    def score(frame: pd.DataFrame) -> np.ndarray:
        z = calibration["slope"] * _logit(raw(frame)) + calibration["intercept"]
        return 1 / (1 + np.exp(-z))

    return score


def _threshold(labels: np.ndarray, scores: np.ndarray) -> float:
    return best_f1_threshold(labels, scores) if labels.any() else 0.5


def _gain(boosting: float | None, logistic: float | None) -> float | None:
    return None if boosting is None or logistic is None else boosting - logistic


def _row(split, scorer, kind, value, m: dict, threshold: float, low=None, high=None) -> dict:
    return {
        "split": split, "scorer": scorer, "slice_kind": kind, "slice_value": value,
        **m, "pr_auc_low": low, "pr_auc_high": high, "threshold": threshold,
    }


def _split_summary(by_split: dict[str, pd.DataFrame], plan: SplitPlan, label_cutoff: datetime, categories: CategoryMap) -> dict:
    """Per split: its window, when its Labels had to be known, Request counts,
    Duplicate Closures within each Label, and Problems training never saw."""
    known_at = plan.labels_known_at(label_cutoff)
    training_problems = set(by_split["train"]["problem"])
    summary = {}
    for name, frame in by_split.items():
        labels = frame["label"]
        summary[name] = {
            "window": plan.as_json()[name],
            "labels_known_at": known_at[name].isoformat(),
            "first_created": frame["created_at_utc"].min().isoformat(),
            "last_created": frame["created_at_utc"].max().isoformat(),
            "requests": len(frame),
            "delayed": int(labels.sum()),
            "duplicate_closures": {
                "not_delayed": int(frame["duplicate_closure"][labels == 0].sum()),
                "delayed": int(frame["duplicate_closure"][labels == 1].sum()),
            },
            "unseen_problem_requests": int((~frame["problem"].isin(training_problems)).sum()),
            "other_problem_requests": int((categories.transform(frame)["problem"] == OTHER).sum()),
        }
    return summary


_METRIC_COLUMNS = (
    "split", "scorer", "slice_kind", "slice_value", "requests", "delayed", "prevalence", "pr_auc", "pr_auc_low",
    "pr_auc_high", "threshold", "precision", "recall", "top10_precision", "top10_recall", "top10_lift", "brier",
)


def _record(conn: SnowflakeConnection, version: str, label_cutoff: datetime, result: Result) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ML.MODEL_EVALUATIONS SELECT %s, %s, %s, %s, CURRENT_TIMESTAMP(), PARSE_JSON(%s), PARSE_JSON(%s), PARSE_JSON(%s)",
        (version, FEATURE_VERSION, result.chosen, label_cutoff,
         json.dumps(result.splits), json.dumps(result.selection), json.dumps(result.model)),
    )
    cur.executemany(
        f"INSERT INTO ML.MODEL_METRICS (model_version, {', '.join(_METRIC_COLUMNS)})"
        f" VALUES ({', '.join(['%s'] * (len(_METRIC_COLUMNS) + 1))})",
        [(version, *(m[c] for c in _METRIC_COLUMNS)) for m in result.metrics],
    )


def main() -> int:
    env = current()
    with connect(env.pipeline_role, env.warehouse, env.database) as conn:
        version = evaluate(conn)
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT split, scorer, requests, prevalence, pr_auc, top10_lift, brier FROM ML.MODEL_METRICS"
            " WHERE model_version = %s AND slice_kind = 'overall' ORDER BY split DESC, scorer",
            (version,),
        ).fetchall()
    print(f"{env.database}: evaluated {version}")
    for split, scorer, n, prevalence, pr_auc, lift, brier in rows:
        print(f"  {split:<10} {scorer:<20} n={n:>9,} prevalence={prevalence:.3f} pr_auc={pr_auc:.3f} lift@10%={lift:.2f} brier={brier:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
