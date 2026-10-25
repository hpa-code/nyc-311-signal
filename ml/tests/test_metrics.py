"""Metric definitions, on worked examples. The numbers a model scores on real
data are measured and reported, not asserted."""

import math

import pytest

from ml.metrics import best_f1_threshold, summarize


def test_a_constant_score_is_no_better_than_prevalence_at_the_top_ten_percent():
    # Every score ties, so the top tenth is filled at random: its expected
    # precision is the prevalence and its lift is 1.
    y = [1, 0, 1, 0, 0, 0, 0, 0, 0, 0]
    m = summarize(y, [0.3] * 10, threshold=0.5)
    assert (m["requests"], m["delayed"], m["prevalence"]) == (10, 2, 0.2)
    assert m["top10_precision"] == pytest.approx(0.2)
    assert m["top10_recall"] == pytest.approx(0.1)
    assert m["top10_lift"] == pytest.approx(1.0)
    assert m["pr_auc"] == pytest.approx(0.2)
    assert (m["precision"], m["recall"]) == (None, 0.0)  # nothing flagged


def test_requests_tied_at_the_top_ten_percent_cutoff_share_its_remaining_places():
    # 20 Requests: the top tenth is 2 places. One score is above the cutoff;
    # three tie for the last place, so each fills a third of it.
    y = [1] + [1, 0, 0] + [1] + [0] * 15
    scores = [0.9] + [0.5] * 3 + [0.1] * 16
    m = summarize(y, scores, threshold=0.5)
    delayed_in_top = 1 + 1 / 3
    assert m["top10_precision"] == pytest.approx(delayed_in_top / 2)
    assert m["top10_recall"] == pytest.approx(delayed_in_top / 3)
    assert m["top10_lift"] == pytest.approx((delayed_in_top / 2) / 0.15)
    # Flagged at the threshold: score >= 0.5, 4 Requests, 2 of them Delayed.
    assert (m["precision"], m["recall"]) == (0.5, pytest.approx(2 / 3))


def test_the_top_ten_percent_rounds_up_to_whole_places():
    # 25 Requests: 3 places. The three highest are Delayed.
    y = [1, 1, 1] + [0] * 22
    m = summarize(y, [0.9, 0.8, 0.7] + [0.1] * 22, threshold=0.5)
    assert (m["top10_precision"], m["top10_recall"]) == (1.0, 1.0)
    assert m["pr_auc"] == pytest.approx(1.0)


def test_brier_score_is_the_mean_squared_error_of_the_probabilities():
    m = summarize([1, 0], [0.8, 0.4], threshold=0.5)
    assert m["brier"] == pytest.approx(0.1)


def test_ranking_metrics_are_missing_when_no_request_was_delayed():
    m = summarize([0, 0, 0], [0.1, 0.2, 0.3], threshold=0.5)
    assert m["prevalence"] == 0.0
    assert m["pr_auc"] is None and m["top10_recall"] is None and m["top10_lift"] is None and m["recall"] is None
    assert not math.isnan(m["brier"])


def test_the_threshold_maximises_f1():
    assert best_f1_threshold([1, 1, 0, 0], [0.9, 0.8, 0.7, 0.1]) == 0.8
