# Exclude NYPD requests from the Modeling Cohort

NYPD is about half of all 311 volume but almost never Delayed (72 of 149,901 in August 2026). Kept in, it adds a large block of trivially easy negatives that inflates PR-AUC and lift without the model learning anything useful. We keep NYPD Requests in analytics and the Overdue Queue, but exclude them from training, evaluation, Admission Scores and the Risk Queue. Every model must also beat an agency-only historical-rate baseline.
