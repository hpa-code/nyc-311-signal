"""Feature version features-v1: the fields ML.FEATURES exposes, all known
when a Request was submitted, and how a model reads them.

Every feature is a category. A level seen in fewer than MIN_LEVEL_REQUESTS
training examples, or never seen in training, is read as OTHER, so a new
Problem is scored like a rare one and counted, never dropped.
"""

from dataclasses import dataclass

import pandas as pd

FEATURE_VERSION = "features-v1"
FEATURES = ("problem", "agency", "borough", "channel", "created_hour", "created_weekday", "created_month")
MIN_LEVEL_REQUESTS = 30
OTHER = "Other"
# Gradient boosting reads at most 255 levels per feature.
MAX_LEVELS = 250


@dataclass(frozen=True)
class CategoryMap:
    """Each feature's training levels: those with at least MIN_LEVEL_REQUESTS
    training examples, at most MAX_LEVELS of the most frequent."""

    levels: dict[str, tuple[str, ...]]

    @classmethod
    def fit(cls, train: pd.DataFrame) -> "CategoryMap":
        levels = {}
        for feature in FEATURES:
            counts = train[feature].astype(str).value_counts()
            kept = counts[counts >= MIN_LEVEL_REQUESTS].index[:MAX_LEVELS]
            levels[feature] = tuple(sorted(kept))
        return cls(levels)

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The features as categories over the training levels plus OTHER."""
        mapped = {}
        for feature in FEATURES:
            values = frame[feature].astype(str)
            known = self.levels[feature]
            mapped[feature] = pd.Categorical(values.where(values.isin(known), OTHER), categories=[*known, OTHER])
        return pd.DataFrame(mapped, index=frame.index)
