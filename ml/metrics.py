"""Metrics for a Delayed score against Labels (instruction.md §11).

A Request is flagged when its score is at or above the threshold. The top 10%
is the ceil(10% of Requests) highest scores. Requests tied at the cutoff
score share the places left: each counts as the fraction of them that fits.
That is the expected value under a random tie-break, so a constant score gets
exactly the prevalence and a lift of 1. A metric with no defined value (no
Delayed Requests, nothing flagged) is None.
"""

import math
from collections.abc import Sequence

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_curve

TOP_FRACTION = 0.10


def summarize(labels: Sequence[int] | np.ndarray, scores: Sequence[float] | np.ndarray, threshold: float) -> dict:
    y = np.asarray(labels, dtype=float)
    s = np.asarray(scores, dtype=float)
    n, delayed = len(y), float(y.sum())
    prevalence = delayed / n if n else None
    flagged = s >= threshold
    top_delayed, places = _top_delayed(y, s)
    top_precision = top_delayed / places if places else None
    return {
        "requests": n,
        "delayed": int(delayed),
        "prevalence": prevalence,
        "pr_auc": float(average_precision_score(y, s)) if 0 < delayed else None,
        "precision": float(y[flagged].mean()) if flagged.any() else None,
        "recall": float(y[flagged].sum() / delayed) if delayed else None,
        "top10_precision": top_precision,
        "top10_recall": top_delayed / delayed if delayed else None,
        "top10_lift": top_precision / prevalence if top_precision is not None and prevalence else None,
        "brier": float(np.mean((s - y) ** 2)) if n else None,
    }


def _top_delayed(y: np.ndarray, s: np.ndarray) -> tuple[float, int]:
    """Expected Delayed Requests in the top 10%, and its number of places."""
    places = math.ceil(TOP_FRACTION * len(y))
    if not places:
        return 0.0, 0
    cutoff = np.sort(s)[::-1][places - 1]
    above, tied = s > cutoff, s == cutoff
    share = (places - above.sum()) / tied.sum()
    return float(y[above].sum() + share * y[tied].sum()), places


def best_f1_threshold(labels: Sequence[int] | np.ndarray, scores: Sequence[float] | np.ndarray) -> float:
    """The score threshold with the highest F1 (the lowest such threshold on a tie)."""
    precision, recall, thresholds = precision_recall_curve(np.asarray(labels), np.asarray(scores, dtype=float))
    f1 = np.divide(2 * precision * recall, precision + recall, out=np.zeros_like(precision), where=precision + recall > 0)
    return float(thresholds[int(np.argmax(f1[:-1]))])


def bootstrap_pr_auc(
    labels: np.ndarray, scores: np.ndarray, resamples: int = 100, seed: int = 0
) -> tuple[float | None, float | None]:
    """95% percentile interval for PR-AUC over Requests resampled with replacement."""
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(resamples):
        i = rng.integers(0, len(labels), len(labels))
        if labels[i].any():
            values.append(average_precision_score(labels[i], scores[i]))
    if not values:
        return None, None
    low, high = np.percentile(values, [2.5, 97.5])
    return float(low), float(high)
