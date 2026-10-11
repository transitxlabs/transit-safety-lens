# Transit Safety Lens: technical note

## 1. Research scope and frozen inputs

This note describes a local, reproducible analysis of published transit safety records. The question is which apparent changes survive record checks, exposure normalisation, small-count shrinkage and definition boundaries. A statistical flag is not a finding of fault.

| dataset | records | accepted | rejected |
| --- | --- | --- | --- |
| modal | 193539 | 193527 | 12 |
| major | 116364 | 116364 | 0 |
| nonmajor | 112896 | 107932 | 4964 |
| fra_security | 3260 | 3260 | 0 |
| exposure | 372801 | 372777 | 24 |

Trace: outputs/inventory.csv; data/manifest.json. Every acquired record is inspected locally. Full incident and non-major exports and original sampled metadata remain untracked. The release includes source checksums, catalogues without cached values and derived aggregates without sensitive incident fields. Events, monthly summaries and exposure records remain distinct units. Full Reporters are not the universe of transit operators.

Keys: monthly records use NTD ID, mode, type of service and calendar month. Major event keys include NTD ID, year and incident number. Non-major keys additionally include event type, location and safety/security classification. Keys with missing components and ambiguous duplicates are quarantined. Record IDs are one-based parsed CSV ordinals; quoted multiline descriptions do not change them.

Input verification checks size and SHA-256 before any calculation. Source metadata and count endpoints are frozen alongside records. Accepted acquisitions reconcile their full parsed record counts and stable before/after release metadata. These checks cannot detect an upstream error consistently reflected in both metadata and records.

<!-- page break -->

## 2. Record checks, missingness and joins

| dataset | rule | failures | denominator | rate |
| --- | --- | --- | --- | --- |
| modal | unknown_mode | 0 | 193539 | 0.0000% |
| modal | duplicate_key | 12 | 193539 | 0.0062% |
| modal | negative_value | 0 | 193539 | 0.0000% |
| modal | fractional_count | 0 | 193539 | 0.0000% |
| modal | missing_safety | 2523 | 193539 | 1.3036% |
| modal | event_total_mismatch | 0 | 191016 | 0.0000% |
| modal | missing_exposure | 472 | 193539 | 0.2439% |
| modal | possible_late_service | 472 | 193539 | 0.2439% |

Trace: outputs/quality_rates.csv, dimension=all, dataset=modal. Duplicate-key rates identify ambiguous publication keys, not proven erroneous submissions. Stratified rates use each rule's eligible denominator, not a shared sample size. Full issue logs retain source records and reasons. Malformed numbers, negative values, fractional event counts, duplicate keys, unknown modes and inconsistent event-category sums are checked separately.

Missing safety counts and exposure are retained as null. Recent safety gaps can reflect validation lag. Missing service values are possible late-reporting proxies; active/inactive mode status is used when present. Actual lateness cannot be identified without submission timestamps. Missing months, invalid records and nonpositive miles exclude a year from inference.

Joins use NTD ID, mode, service and month, with explicit many-to-one validation. Ambiguous target keys are removed from joins and reported as losses rather than multiplying source events. Every source event and independent exposure record gets a join outcome; outputs/join_summary.csv counts matches and losses. Source coverage differs, so a loss is not automatically a source error.

<!-- page break -->

## 3. Definition regimes and metric lineage

Trace: data/definitions/timeline.json and outputs/monthly_provenance.csv.gz. Each monthly rate retains its source release/hash, record, rule list and applicable definition regime. Annual period rows and prior-fit rows retain their constituent source IDs; credible_change.csv points to the same IDs and prior group.

The timeline separates reporting thresholds, event/person categories, mode/service transitions and retrospective publication changes. It includes the 2011/2015 continuity regimes, 2016 person attribution, expanded 2023 worker-assault scope, 2019 service codes and current manual clarifications. Sources are the official FTA product notes, safety manuals and portal exposure metadata/read-me.

The 2024, 2025 and 2026 annual guards are conservative analysis boundaries where exact implementation dates remain unresolved. No inference silently stitches observations across such a boundary. Publication reclassification need not change total events, and overlapping trespasser subtotals are never added to mutually exclusive person categories.

A metric can cross a date only when its own definition is unaffected and the timeline records that scope. Unit tests check both blocked total-event comparisons and person-category boundaries. Recent total-event comparisons are explicitly unavailable under these guards. The timeline is not a complete legal chronology.

FTA workbook and original manual downloads returned access errors in the direct route; the browser workbook download did not produce a verified file. The current portal exports are complete, but the safety records unique to the unacquired 2002-2013 workbooks and raw unadjusted exposure workbook are not frozen here. Two overlapping historical releases were not obtained, so empirical revision rates are unavailable. A fresh export of the same release is not a historical release. Actual submission timestamps are not published in these inputs, so possible late service is a missingness proxy. The timeline uses conservative annual guards where exact implementation dates remain unverified. Public release requires the Director's check.

<!-- page break -->

## 4. Exposure-offset hierarchy and error rule

Trace: outputs/model_config.json and outputs/priors.csv. For agency/mode i and period t, Y_it follows Poisson(E_it times lambda_it). E_it is vehicle revenue miles in millions. Within mode and earlier agency-size band, lambda_it has Gamma(shape, rate) prior. Training-only marginal likelihood integrates out rates to estimate each peer group's gamma parameters; small or all-zero peer groups use a declared weak proper fallback.

With event count y and exposure e, the posterior is Gamma(shape+y, rate+e). Conditional independent period posteriors give an analytic beta-prime rate-ratio distribution. Equal-tailed posterior intervals and sign probabilities are computed without simulation noise. Training year is 2019; the main periods are 2021 and 2022, and the held-out flag periods are 2020 and 2021.

Agency size uses earlier published annual service: under one million, one to ten million, or over ten million vehicle revenue miles. Types of service are summed within a mode only after each observed constituent passes calendar coverage checks. Service composition must match across periods. Unknown training groups remain unavailable.

A flag requires the 95% ratio interval to exclude one. Within all eligible agency/mode comparisons, order candidates by posterior false-sign probability and retain a prefix whose mean is at most 0.05. This controls posterior expected false-sign proportion conditional on the model and estimated hyperparameters. It is not frequentist false-discovery control and is not distribution-free. Hyperparameter and exposure uncertainty are not integrated.

These records cover NTD Full Reporters in the acquired products. They do not measure all U.S. transit incidents, under-reporting, individual passenger risk or agency fault. Reporting thresholds, validation lag, retrospective reclassification, sampled ridership and adjusted exposure can alter apparent trends. Missing months are not zeros. The model assumes independent Poisson counts conditional on rate and exposure, with a gamma hierarchy for between-agency differences. It does not model within-agency temporal clustering, pandemic effects, changes in reporting practice or uncertainty in exposure. Hyperparameters are estimated, so intervals and error control are conditional on those estimates. Poor empirical coverage is evidence against using these intervals for operational decisions. No causal or real-world safety conclusion follows from a flag.

<!-- page break -->

## 5. Results, baselines and held-out check

Trace: outputs/summary.json model. Eligible comparisons: 893; unavailable: 663; flags: 73; increases: 55; decreases: 18. The comparison universe includes every agency/mode key observed in the acquired safety products, including keys with no target-period monthly totals and explicit unavailable rows. FRA-regulated security-only records are not silently combined with other safety totals.

| baseline | direction | naive_flags | kept | dropped | added |
| --- | --- | --- | --- | --- | --- |
| raw_count | rate increase | 351 | 55 | 296 | 0 |
| raw_count | rate decrease | 224 | 18 | 206 | 0 |
| raw_rate | rate increase | 374 | 55 | 319 | 0 |
| raw_rate | rate decrease | 261 | 18 | 243 | 0 |

Trace: outputs/baseline_comparison.csv. A naive flag is any nonzero year-over-year difference in counts or rates on the same eligible universe. Kept/dropped/added are direction-specific; an opposite credible direction is also counted separately. These arithmetic identities are tested. Baselines do not supply error control.

Trace: outputs/summary.json heldout and outputs/heldout.csv. Conditional stationary predictive intervals cover 742 of 818 outcomes (90.709%). Among 64 held-out eligible flags, 57 persist in raw-rate direction relative to 2020 (89.062%). This checks whether the direction remains in 2022, not whether the annual change repeats.

The held-out year does not fit priors or choose flags. Prediction uses a 2021 posterior and observed 2022 exposure, so it is exposure-conditional rather than an advance forecast of service. Calibration under simulated model-consistent data is a unit test; empirical held-out coverage can be poor because stationarity and Poisson assumptions fail. Report that failure directly rather than calling persistence proof of safety change.

<!-- page break -->

## 6. Reproduction, release limits and evidence map

Use Python 3.14 and the pinned dependencies in pyproject.toml. Install with `python -m pip install -e '.[dev]'`. Then `make rebuild` verifies frozen input hashes, processes every acquired record, and regenerates outputs, this README, the technical note and static page without network. `make check` runs lint, types and network-blocked fixtures. `python scripts/replay.py --destination work-tmp/replay-new --local-inputs .` exports the committed checkout and checks every rebuilt output hash against the committed output manifest. Use a new destination for every replay. Full rebuild and replay require the excluded local frozen inputs at their manifest paths; replay stages only size- and checksum-verified local inputs into the clean archive. The aggregate checkout alone cannot replay incident audits. The separate acquisition script requires official-source network access and a new ignored work-tmp destination; it never runs during rebuild or CI. A future download is a new release and cannot substitute for the recorded frozen bytes.

Evidence map: inventory.csv covers acquisition accounting; quality_rates.csv covers aggregate rule failures and eligible denominators; join_summary.csv and exposure_join_summary.csv cover reconciliation. Full quality issue and incident/non-major join logs remain local and untracked. monthly_provenance.csv.gz, annual_panel.csv and priors.csv cover monthly aggregate lineage; credible_change.csv and baseline_comparison.csv cover main results; heldout_training_flags.csv and heldout.csv cover the temporal test; recent_comparison_exclusions.csv covers definition and completeness blocks.

outputs/checksums.json records deterministic output hashes. docs/replay_receipt.json records the exact committed checkout tested and the comparison outcome. The replay uses preinstalled pinned dependencies and disables network calls. It does not prove that external future source releases will have the same schema or content. CI runs only small fixtures with network access disabled for the checks.

Original FTA workbook/PDF retrieval, older safety record coverage and a genuine overlapping historical-release pair remain acquisition dependencies. Revision-difference code has adversarial fixtures, but no empirical revision rate is claimed. Public release, operational use and agency interpretation remain outside this local package's proof state.

Attribution and rights are in DATA.md, NOTICE and data/manifest.json. Code is Apache-2.0; official records retain source terms. The package makes no priority claim and implies no government endorsement.
