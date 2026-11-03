# Temporal validation intake for the fixed CreditFlow model

**Status:** data and independent review are unavailable. This is an intake
contract, not a backtest result or approval for lending use. The committed
`data/creditflow_dataset.csv` is synthetic and cannot satisfy this contract.

## Data required from the approved owner

Provide one row per application with a stable pseudonymous `application_id`,
an `application_at` timestamp, the eight canonical feature values **as known at
application time**, a binary `default` outcome, and `label_matured_at` (the time
the full outcome horizon was observed and the label finalized). Supply the
approved default definition, outcome horizon, observation cutoff, population
inclusion/exclusion rules, source system, permitted use, and a SHA-256 manifest.
The three money fields must follow the serving contract in VND; the model
artifact converts them to its training scale. Do not include names, contact
details, or account numbers in the analysis extract.

The owner must specify how denied and withdrawn applications are treated. A
default label observed only for funded loans creates selection bias; report the
coverage and exclusion counts rather than treating missing outcomes as `0`.
Any source-to-canonical mapping must document the original field, unit, time
available, transformation, and missingness. The current German Credit mapping
leaves several required canonical fields unavailable and is not a substitute.

## Checks before scoring

1. Confirm source approval and match the input file to its manifest hash.
2. Reject duplicate application IDs, missing dates, invalid labels, or records
   whose `label_matured_at` is after the declared observation cutoff. Confirm
   the chosen horizon has elapsed for every included application.
3. Prove every feature was available at `application_at`. Exclude post-decision
   fields, outcome-derived fields, and data updated after origination.
4. Freeze the model bundle, threshold, feature mapping, and evaluation code
   before reading the later cohort outcomes. Record their hashes and versions.
5. Define non-overlapping calendar cohorts before inspecting performance.
   Report row counts, label coverage, missingness, and default prevalence for
   each cohort. Stop if the approved minimum sample or class counts are unmet.

## Independent evaluation output

An independent Risk reviewer should reproduce ROC-AUC, PR-AUC, KS, Brier score,
calibration bins/ECE, confusion matrix, and the APPROVE/REVIEW/REJECT mix using
the **unchanged** production bundle and policy thresholds. Include uncertainty
intervals and cohort-by-cohort results; aggregate metrics alone can hide decay.
Any business-cost calculation needs owner-approved real cost assumptions rather
than the synthetic model card's relative FN/FP weights. Subgroup diagnostics
require separately approved group data and methodology.

The evidence package should contain the redacted data manifest, extract and
label definitions, cohort boundaries, model and code hashes, dependency
versions, run command/log, metric tables, exceptions, and dated reviewer
decision. The reviewer must be independent of model development. A passing
script or good synthetic metric is not an approval decision.

## Current stop condition

No approved time-stamped application/outcome extract or independent reviewer
has been supplied. Therefore no temporal backtest or independent validation
result is reported for the current model.
