# NYC 311 Signal

Collects NYC 311 Service Requests, tracks how they change, and predicts which new ones will be Delayed, so analysts can review closure patterns and risk.

## Requests

**Service Request**:
One request submitted to NYC 311, identified by its `unique_key`. Short form: Request.
_Avoid_: Complaint, ticket, case

**Problem**:
The category of a Service Request (NYC's current label for the API field `complaint_type`).
_Avoid_: Complaint type, category

**Problem Detail**:
The sub-category under a Problem (API field `descriptor`).
_Avoid_: Descriptor

**Closure**:
The administrative closing of a Service Request as recorded by the source, including closing it as a duplicate of another Request. It does not mean the physical issue was fixed.
_Avoid_: Resolution, fix

## Observing the source

**Observation**:
One copy of a Service Request as fetched from the source at a specific time. Many Observations may exist for one Request.
_Avoid_: Event, record, snapshot

**Request Version**:
One accepted, distinct state of a Service Request, derived from Observations.
_Avoid_: Revision, update

**Current Request**:
The latest observed Request Version of a Service Request. It is "latest observed", not a guarantee of the source's true state.
_Avoid_: Latest record, master record

**First Observed**:
The moment the system first fetched any Observation of a Service Request; distinct from its creation time.

**Reconciliation Pass**:
The weekly re-fetch of older creation dates that finds changes the change cursor missed, and takes them in. Not to be confused with a collector run reconciling with its manifest.

**Quarantined Observation**:
An Observation rejected for failing data-quality rules, kept with its reason.
_Avoid_: Bad record, error row

## Outcome and prediction

**Seven-Day Horizon**:
168 elapsed hours after a Service Request's creation time. A project-defined threshold, not a city service-level agreement.
_Avoid_: SLA, deadline

**Delayed**:
The outcome of a Service Request that was not Closed within the Seven-Day Horizon. Known only once its Label has matured.
_Avoid_: Late, slow, missed SLA

**Overdue**:
The observed status of a Service Request that is still open past its Seven-Day Horizon. A fact, not a prediction.
_Avoid_: Delayed (when meaning currently open), late

**Label**:
The Delayed (1) or not-Delayed (0) outcome assigned to a Service Request from a trusted Observation taken after its Seven-Day Horizon plus a reporting allowance. Requests whose outcome cannot be trusted have no Label.
_Avoid_: Target, ground truth

**Modeling Cohort**:
The Service Requests eligible for training, evaluation and Admission Scores. Excludes NYPD Requests.
_Avoid_: Dataset, sample

**Matured Label**:
A Label whose outcome was knowable at a given point in time.

**Date-Only Closure**:
A Closure recorded with a date but no meaningful time of day. Judged by calendar day; one falling on the same day as the Seven-Day Horizon yields no Label.
_Avoid_: Midnight closure

**Duplicate Closure**:
A Closure recording that the Service Request duplicates another one, whether or not the original is still open. Labeled like any other Closure: one after the Seven-Day Horizon is Delayed.

**Reopened Request**:
A Service Request observed Closed and later observed open again. Treated as an exception and excluded from training.

**Admission Score**:
The Delayed probability computed once for a Service Request shortly after it is First Observed, and never silently recomputed.
_Avoid_: Risk score (unqualified), priority

**Risk Queue**:
Recent Service Requests in the Modeling Cohort ordered by Admission Score. Predicted delay is not urgency or deserved priority.
_Avoid_: Priority queue, triage list

**Overdue Queue**:
Service Requests that are currently Overdue, kept separate from the Risk Queue.

## Publishing

**Publication**:
One complete, validated release of dashboard data and Admission Scores, shown together. The dashboard always shows exactly one Publication.
_Avoid_: Refresh, load, run
