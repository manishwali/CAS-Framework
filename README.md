# CAS Framework

A generic, portable **Customer Adoption Signal (CAS)** scoring engine for B2B SaaS customer
health/churn-risk tracking. Extracted from a larger internal customer-analytics tool; this repo
contains only the reusable scoring logic and API shapes -- no account data, no customer names,
no integration-specific code.

## What's here

- **`cas_scoring.py`** -- pure functions, no I/O, no framework dependency. Two layers:
  - **Snapshot**: a weighted composite (0-100) across however many signals you can source today,
    reweighting automatically around whatever's missing, with a minimum-coverage gate so a score
    built from a sliver of signals is never presented as a full one.
  - **Trajectory**: a same-calendar-month year-over-year read on direction (Improving / Stable /
    Contracting), as the equal-weight mean of whichever trend signals you have.
  - Tiering (THRIVING / HEALTHY / DEVELOPING / AT RISK / CRITICAL), a "weakest signals" lookup
    for explaining a low score, and a `overlay_live()` helper for merging a live pull over a
    cached baseline without ever blanking a known value with a failed one.
- **`schemas.py`** -- Pydantic response shapes for exposing the above over an API, including the
  display-text/partial/coverage conventions so a frontend never has to re-derive formatting logic.

## What's NOT here, by design

No data files, no per-account configuration, no source-system integration code (Snowflake/
Salesforce/Pendo/etc. connectors), no test fixtures with real customer names. The
`SNAPSHOT_SIGNALS` / `TRAJECTORY_SIGNALS` registries in `cas_scoring.py` are a **template** --
replace the ids, names, weights and source descriptions with your own before using this for
real scoring. The math around them (composite calculation, reweighting, publish threshold,
trajectory averaging, weakest-signal lookup) is the part meant to be reused as-is.

## Usage

```python
from cas_scoring import snapshot, trajectory, evaluate

scores = {'S1': 72, 'S2': 65, 'S9': 58}   # however many signals you have for this account
result = evaluate(snapshot_scores=scores, trajectory_scores={'T4': 80, 'T8': 55})
print(result['score'], result['tier'])   # e.g. 67 'HEALTHY'
```
