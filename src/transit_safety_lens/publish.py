"""Generate the research documentation and static page from audited output tables."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


def table_markdown(frame: pd.DataFrame) -> str:
    columns = frame.columns.tolist()
    rows = ["| " + " | ".join(map(str, columns)) + " |",
            "| " + " | ".join("---" for _ in columns) + " |"]
    rows.extend("| " + " | ".join(str(value).replace("|", "/") for value in row) + " |"
                for row in frame.itertuples(index=False, name=None))
    return "\n".join(rows)


def render_release(root: Path, summary: dict[str, Any], output: Path) -> None:
    manifest = json.loads((root / "data/manifest.json").read_text())
    inv = pd.read_csv(output / "inventory.csv")
    quality = pd.read_csv(output / "quality_rates.csv")
    baseline = pd.read_csv(output / "baseline_comparison.csv")
    comparisons = pd.read_csv(output / "credible_change.csv", dtype={"ntd_id": str})
    eligible = comparisons.loc[comparisons["eligible"]]
    examples = eligible.sort_values(["ntd_id", "mode"]).head(3)
    examples = examples[["ntd_id", "mode", "events0", "events1", "miles0", "miles1",
                         "ratio_lower", "ratio_median", "ratio_upper", "flag"]].copy()
    for column in ["ratio_lower", "ratio_median", "ratio_upper"]:
        examples[column] = examples[column].map(lambda x: f"{x:.3g}")
    primary_rules = ["missing_safety", "missing_exposure", "possible_late_service", "duplicate_key",
                     "negative_value", "fractional_count", "event_total_mismatch", "unknown_mode"]
    quality_all = quality.loc[(quality["dimension"] == "all") & quality["rule"].isin(primary_rules),
                              ["dataset", "rule", "failures", "denominator", "rate"]].copy()
    quality_all["rate"] = quality_all["rate"].map(lambda x: f"{100*x:.4f}%" if pd.notna(x) else "unavailable")
    model, held = summary["model"], summary["heldout"]
    coverage = f"{held['coverage']:.3%}" if held["coverage"] is not None else "unavailable"
    persistence = f"{held['persistence']:.3%}" if held["persistence"] is not None else "unavailable"
    limits = (
        "These records cover NTD Full Reporters in the acquired products. They do not measure all "
        "U.S. transit incidents, under-reporting, individual passenger risk or agency fault. "
        "Reporting thresholds, validation lag, retrospective reclassification, sampled ridership "
        "and adjusted exposure can alter apparent trends. Missing months are not zeros. "
        "The model assumes independent Poisson counts conditional on rate and exposure, with a gamma "
        "hierarchy for between-agency differences. It does not model within-agency temporal clustering, "
        "pandemic effects, changes in reporting practice or uncertainty in exposure. Hyperparameters "
        "are estimated, so intervals and error control are conditional on those estimates. "
        "Poor empirical coverage is evidence against using these intervals for operational decisions. "
        "No causal or real-world safety conclusion follows from a flag."
    )
    gaps = (
        "FTA workbook and original manual downloads returned access errors in the direct route; "
        "the browser workbook download did not produce a verified file. The current portal exports "
        "are complete, but the safety records unique to the unacquired 2002-2013 workbooks and raw unadjusted exposure workbook "
        "are not frozen here. Two overlapping historical releases were not obtained, so empirical "
        "revision rates are unavailable. A fresh export of the same release is not a historical "
        "release. Actual submission timestamps are not published in these inputs, so possible late "
        "service is a missingness proxy. The timeline uses conservative annual guards where exact "
        "implementation dates remain unverified. The public release followed an internal release check on 2026-10-11."
    )
    rebuild = (
        "Use Python 3.14 and the pinned dependencies in pyproject.toml. Install with "
        "`python -m pip install -e '.[dev]'`. Then `make rebuild` verifies frozen input hashes, "
        "processes every acquired record, and regenerates outputs, this README, the technical note "
        "and static page without network. `make check` runs lint, types and network-blocked fixtures. "
        "`python scripts/replay.py --destination work-tmp/replay-new --local-inputs .` exports the committed checkout "
        "and checks every rebuilt output hash against the committed output manifest. Use a new "
        "destination for every replay. Full rebuild and replay require the excluded local frozen "
        "inputs at their manifest paths; replay stages only size- and checksum-verified local inputs "
        "into the clean archive. The aggregate checkout alone cannot replay incident audits. "
        "The separate acquisition script requires official-source network access and a new ignored "
        "work-tmp destination; it never runs during rebuild or CI. A future download is a new release "
        "and cannot substitute for the recorded frozen bytes."
    )
    readme = f"""# Transit Safety Lens

TransitX Labs Inc. is building a reproducible public transit safety data research system. This package measures the completeness, consistency and comparability of published National Transit Database records, and estimates exposure-normalised changes under a stated statistical model. It supports safety data management by making missingness, exclusions and source lineage inspectable.

**Status: public aggregate release (2026-10-11), with incident and non-major raw rows excluded from tracking. Acquisition and revision gaps remain; held-out coverage is below nominal and flags remain exploratory.**

## Frozen record coverage

{table_markdown(inv[['dataset','records','accepted','rejected','earliest','latest']])}

Source counts, bytes, release timestamps, terms and SHA-256 hashes are in [data/manifest.json](data/manifest.json). Every acquired row is checked. The scope is the acquired official products, with the coverage gaps listed below.

## Change results and denominators

The model fits peer-group gamma priors to calendar year {model['training_year']}, then compares {model['comparison_years'][0]} with {model['comparison_years'][1]}. It retains {model['eligible_comparisons']} agency/mode comparisons and excludes {model['unavailable_comparisons']}. Under the conditional posterior rule, {model['flags']} comparisons receive a flag: {model['rate_increase']} increases and {model['rate_decrease']} decreases. These are statistical statements about reported event rates.

{table_markdown(baseline)}

Examples are three eligible agency/mode keys selected in sorted order, not a ranking. Miles are vehicle revenue miles; ratios are later-period / earlier-period reported-event rates. Ratio intervals have 95% posterior probability under the fitted model.

{table_markdown(examples)}

The held-out comparison fits on earlier data, flags 2020-2021 changes, and evaluates 2022. Predictive intervals cover {held['covered']} of {held['eligible']} eligible outcomes ({coverage}). Of {held['flagged']} eligible flagged changes, {held['persisted']} retain the same raw-rate direction relative to 2020 ({persistence}). Empirical predictive coverage is below the nominal 95% level. Flags remain exploratory; persistence alone does not validate interval calibration. See [outputs/heldout.csv](outputs/heldout.csv).

All numbers above are generated from outputs/summary.json, inventory.csv, baseline_comparison.csv and credible_change.csv. The status field in outputs/summary.json labels the kind of output; the release status is stated in this README. [The technical note](docs/TECHNICAL_NOTE.md) explains the assumptions and [six-page PDF](docs/TECHNICAL_NOTE.pdf) provides a printable companion.

## Quality, provenance and definitions

All published field availability is measured as aggregate counts in outputs/field_completeness.csv.gz, including optional or redacted fields; nulls there are not automatically errors. Catalogue schemas and aggregate field names describe the audit without exposing source field values. See [outputs/quality_rates.csv](outputs/quality_rates.csv) for rule-specific numerators and eligible denominators by year, mode and agency size. The full quality_issues.csv and incident/non-major join logs remain local and untracked; public join_summary.csv and exposure_join_summary.csv retain aggregate match and loss counts. Possible late service is a proxy, not a measured late-submission rate. Source rows are identified by dataset and one-based CSV record ordinal, not physical line number. outputs/monthly_provenance.csv.gz links monthly aggregate rates to source hashes, records, rules and definition regimes; annual_panel.csv and priors.csv retain the monthly record lists used by comparisons.

[data/definitions/timeline.json](data/definitions/timeline.json) cites thresholds, categories, mode and service-code transitions, and publication revisions. Tests require a comparison to stay within one applicable regime. Recent comparisons blocked by conservative definition guards are listed in outputs/recent_comparison_exclusions.csv.

## Limits and acquisition gaps

{limits}

{gaps}

## Rebuild and checks

{rebuild}

Code: Apache-2.0. Data: source-specific terms, including an unresolved non-major licence, in [DATA.md](DATA.md). Attribution: Federal Transit Administration, U.S. Department of Transportation, National Transit Database. No government endorsement is implied.
"""
    (root / "README.md").write_text(readme)
    terms_rows = []
    for source in manifest["sources"]:
        terms_rows.append(dict(file=source["path"], release=source["release_date"] or "unavailable", bytes=source["bytes"],
                               sha256=source["sha256"], terms=source["terms"], url=source["url"]))
    data_md = "# Data sources, terms and attribution\n\n" + (
        "Official source records retain their terms. Four portal datasets declare PUBLIC_DOMAIN or "
        "USGOV_WORKS. The non-major dataset points to an unknown-licence designation; its reuse rights require verification before public redistribution. Official attribution identifies the Federal Transit Administration. The original "
        "metadata files preserve the stated public-domain link and public access designation. "
        "Code licensing does not replace source terms. Attribution: Federal Transit Administration, "
        "U.S. Department of Transportation, National Transit Database. No endorsement is implied.\n\n"
        "CSV files retain all fields from complete reconciled exports. The major-event convenience CSV is a lossless normalised concatenation of frozen official pages, with public row identities checked for uniqueness. Its constituent page URLs, counts and hashes are also retained. Stored gzip streams are deterministic and "
        "decompress to the original CSV bytes; both hashes and byte sizes are recorded. Counts are "
        "reconciled to the source count endpoint. Before/after metadata confirms that rowsUpdatedAt "
        "did not change during each accepted acquisition. This is a consistency check, not an atomic "
        "snapshot guarantee. Release timestamps are portal rowsUpdatedAt, not event dates or formal "
        "certification dates. Official explanatory attachments retain their source terms.\n\n"
        "Distribution: major-event and FRA incident exports and major-event pages remain local "
        "and untracked because they contain sensitive incident details. Non-major rows remain "
        "local and untracked because redistribution rights are unresolved. Original metadata "
        "also remains local because its cached samples contain source values; data/catalog "
        "retains catalogue descriptions and schemas with cached values removed. The manifest "
        "records original hashes, sizes and distribution status. Incident/non-major record-level "
        "join logs, full quality issue logs and revision changes remain local and untracked. "
        "Released derived tables contain no incident narratives, person types, addresses or "
        "coordinates. Field-completeness tables contain only field names and aggregate counts.\n\n"
    ) + table_markdown(pd.DataFrame(terms_rows)) + "\n\n## Coverage and unresolved acquisition\n\n" + gaps + "\n"
    (root / "DATA.md").write_text(data_md)
    sections = [
        ("1. Research scope and frozen inputs", [
            "This note describes a local, reproducible analysis of published transit safety records. The question is which apparent changes survive record checks, exposure normalisation, small-count shrinkage and definition boundaries. A statistical flag is not a finding of fault.",
            table_markdown(inv[["dataset", "records", "accepted", "rejected"]]),
            "Trace: outputs/inventory.csv; data/manifest.json. Every acquired record is inspected locally. Full incident and non-major exports and original sampled metadata remain untracked. The release includes source checksums, catalogues without cached values and derived aggregates without sensitive incident fields. Events, monthly summaries and exposure records remain distinct units. Full Reporters are not the universe of transit operators.",
            "Keys: monthly records use NTD ID, mode, type of service and calendar month. Major event keys include NTD ID, year and incident number. Non-major keys additionally include event type, location and safety/security classification. Keys with missing components and ambiguous duplicates are quarantined. Record IDs are one-based parsed CSV ordinals; quoted multiline descriptions do not change them.",
            "Input verification checks size and SHA-256 before any calculation. Source metadata and count endpoints are frozen alongside records. Accepted acquisitions reconcile their full parsed record counts and stable before/after release metadata. These checks cannot detect an upstream error consistently reflected in both metadata and records."]),
        ("2. Record checks, missingness and joins", [
            table_markdown(quality_all.loc[quality_all["dataset"] == "modal"]),
            "Trace: outputs/quality_rates.csv, dimension=all, dataset=modal. Duplicate-key rates identify ambiguous publication keys, not proven erroneous submissions. Stratified rates use each rule's eligible denominator, not a shared sample size. Full issue logs retain source records and reasons. Malformed numbers, negative values, fractional event counts, duplicate keys, unknown modes and inconsistent event-category sums are checked separately.",
            "Missing safety counts and exposure are retained as null. Recent safety gaps can reflect validation lag. Missing service values are possible late-reporting proxies; active/inactive mode status is used when present. Actual lateness cannot be identified without submission timestamps. Missing months, invalid records and nonpositive miles exclude a year from inference.",
            "Joins use NTD ID, mode, service and month, with explicit many-to-one validation. Ambiguous target keys are removed from joins and reported as losses rather than multiplying source events. Every source event and independent exposure record gets a join outcome; outputs/join_summary.csv counts matches and losses. Source coverage differs, so a loss is not automatically a source error."]),
        ("3. Definition regimes and metric lineage", [
            "Trace: data/definitions/timeline.json and outputs/monthly_provenance.csv.gz. Each monthly rate retains its source release/hash, record, rule list and applicable definition regime. Annual period rows and prior-fit rows retain their constituent source IDs; credible_change.csv points to the same IDs and prior group.",
            "The timeline separates reporting thresholds, event/person categories, mode/service transitions and retrospective publication changes. It includes the 2011/2015 continuity regimes, 2016 person attribution, expanded 2023 worker-assault scope, 2019 service codes and current manual clarifications. Sources are the official FTA product notes, safety manuals and portal exposure metadata/read-me.",
            "The 2024, 2025 and 2026 annual guards are conservative analysis boundaries where exact implementation dates remain unresolved. No inference silently stitches observations across such a boundary. Publication reclassification need not change total events, and overlapping trespasser subtotals are never added to mutually exclusive person categories.",
            "A metric can cross a date only when its own definition is unaffected and the timeline records that scope. Unit tests check both blocked total-event comparisons and person-category boundaries. Recent total-event comparisons are explicitly unavailable under these guards. The timeline is not a complete legal chronology.",
            gaps]),
        ("4. Exposure-offset hierarchy and error rule", [
            "Trace: outputs/model_config.json and outputs/priors.csv. For agency/mode i and period t, Y_it follows Poisson(E_it times lambda_it). E_it is vehicle revenue miles in millions. Within mode and earlier agency-size band, lambda_it has Gamma(shape, rate) prior. Training-only marginal likelihood integrates out rates to estimate each peer group's gamma parameters; small or all-zero peer groups use a declared weak proper fallback.",
            "With event count y and exposure e, the posterior is Gamma(shape+y, rate+e). Conditional independent period posteriors give an analytic beta-prime rate-ratio distribution. Equal-tailed posterior intervals and sign probabilities are computed without simulation noise. Training year is 2019; the main periods are 2021 and 2022, and the held-out flag periods are 2020 and 2021.",
            "Agency size uses earlier published annual service: under one million, one to ten million, or over ten million vehicle revenue miles. Types of service are summed within a mode only after each observed constituent passes calendar coverage checks. Service composition must match across periods. Unknown training groups remain unavailable.",
            "A flag requires the 95% ratio interval to exclude one. Within all eligible agency/mode comparisons, order candidates by posterior false-sign probability and retain a prefix whose mean is at most 0.05. This controls posterior expected false-sign proportion conditional on the model and estimated hyperparameters. It is not frequentist false-discovery control and is not distribution-free. Hyperparameter and exposure uncertainty are not integrated.",
            limits]),
        ("5. Results, baselines and held-out check", [
            f"Trace: outputs/summary.json model. Eligible comparisons: {model['eligible_comparisons']}; unavailable: {model['unavailable_comparisons']}; flags: {model['flags']}; increases: {model['rate_increase']}; decreases: {model['rate_decrease']}. The comparison universe includes every agency/mode key observed in the acquired safety products, including keys with no target-period monthly totals and explicit unavailable rows. FRA-regulated security-only records are not silently combined with other safety totals.",
            table_markdown(baseline[["baseline", "direction", "naive_flags", "kept", "dropped", "added"]]),
            "Trace: outputs/baseline_comparison.csv. A naive flag is any nonzero year-over-year difference in counts or rates on the same eligible universe. Kept/dropped/added are direction-specific; an opposite credible direction is also counted separately. These arithmetic identities are tested. Baselines do not supply error control.",
            f"Trace: outputs/summary.json heldout and outputs/heldout.csv. Conditional stationary predictive intervals cover {held['covered']} of {held['eligible']} outcomes ({coverage}). Among {held['flagged']} held-out eligible flags, {held['persisted']} persist in raw-rate direction relative to 2020 ({persistence}). This checks whether the direction remains in 2022, not whether the annual change repeats.",
            "The held-out year does not fit priors or choose flags. Prediction uses a 2021 posterior and observed 2022 exposure, so it is exposure-conditional rather than an advance forecast of service. Calibration under simulated model-consistent data is a unit test; empirical held-out coverage can be poor because stationarity and Poisson assumptions fail. Report that failure directly rather than calling persistence proof of safety change."]),
        ("6. Reproduction, release limits and evidence map", [
            rebuild,
            "Evidence map: inventory.csv covers acquisition accounting; quality_rates.csv covers aggregate rule failures and eligible denominators; join_summary.csv and exposure_join_summary.csv cover reconciliation. Full quality issue and incident/non-major join logs remain local and untracked. monthly_provenance.csv.gz, annual_panel.csv and priors.csv cover monthly aggregate lineage; credible_change.csv and baseline_comparison.csv cover main results; heldout_training_flags.csv and heldout.csv cover the temporal test; recent_comparison_exclusions.csv covers definition and completeness blocks.",
            "outputs/checksums.json records deterministic output hashes. docs/replay_receipt.json records the exact committed checkout tested and the comparison outcome. The replay uses preinstalled pinned dependencies and disables network calls. It does not prove that external future source releases will have the same schema or content. CI runs only small fixtures with network access disabled for the checks.",
            "Original FTA workbook/PDF retrieval, older safety record coverage and a genuine overlapping historical-release pair remain acquisition dependencies. Revision-difference code has adversarial fixtures, but no empirical revision rate is claimed. Public release, operational use and agency interpretation remain outside this local package's proof state.",
            "Attribution and rights are in DATA.md, NOTICE and data/manifest.json. Code is Apache-2.0; official records retain source terms. The package makes no priority claim and implies no government endorsement."])
    ]
    markdown = "# Transit Safety Lens: technical note\n\n" + "\n\n<!-- page break -->\n\n".join(
        "## " + title + "\n\n" + "\n\n".join(paragraphs) for title, paragraphs in sections) + "\n"
    (root / "docs/TECHNICAL_NOTE.md").write_text(markdown)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 11
    styles["Normal"].leading = 15
    story = []
    for number, (title, paragraphs) in enumerate(sections):
        if number:
            story.append(PageBreak())
        story.append(Paragraph(html.escape(title), styles["Heading1"]))
        for paragraph in paragraphs:
            if paragraph.startswith("| "):
                # Compact text rows avoid squeezed multi-column cells in a narrow page.
                lines = paragraph.splitlines()
                headers = [x.strip() for x in lines[0].strip("|").split("|")]
                rendered = []
                for line in lines[2:]:
                    cells = [x.strip() for x in line.strip("|").split("|")]
                    rendered.append("; ".join(f"{k}: {v}" for k, v in zip(headers, cells, strict=True)))
                text = "<br/>".join(html.escape(row) for row in rendered)
            else:
                text = html.escape(paragraph).replace("`", "")
            story.append(Paragraph(text, styles["Normal"]))
            story.append(Spacer(1, 8))
    def footer(canvas: Any, document: Any) -> None:
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#344054"))
        canvas.drawString(0.65 * inch, 0.4 * inch, "Transit Safety Lens | public research package")
        canvas.drawRightString(7.85 * inch, 0.4 * inch, str(document.page))
    document = SimpleDocTemplate(str(root / "docs/TECHNICAL_NOTE.pdf"), pagesize=(8.5 * inch, 11 * inch),
                                 rightMargin=0.65 * inch, leftMargin=0.65 * inch,
                                 topMargin=0.65 * inch, bottomMargin=0.65 * inch,
                                 title="Transit Safety Lens: technical note", author="TransitX Labs Inc.",
                                 invariant=1)
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    body = f"""<!doctype html>
<html lang="en-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Reproducible checks and bounded statistical comparisons for public transit safety records."><title>Transit Safety Lens | TransitX Labs</title><link rel="stylesheet" href="style.css"></head>
<body><a class="skip" href="#main">Skip to content</a><header><p>TransitX Labs Inc. / research</p><h1>Transit Safety Lens</h1><p>Trace public transit safety metrics to their records, checks and reporting definitions.</p></header>
<main id="main"><p class="status"><strong>Public aggregate release (2026-10-11).</strong> Raw incident and non-major rows remain local and untracked. Acquisition gaps remain. Held-out predictive coverage is below nominal; flags remain exploratory.</p><section><h2>What the records support</h2><p>{html.escape(limits)}</p></section>
<section><h2>Model-conditional change results</h2><p>The 2021-2022 comparison includes {model['eligible_comparisons']} eligible agency/mode pairs. It flags {model['rate_increase']} reported-rate increases and {model['rate_decrease']} decreases under the stated posterior rule. These are statistical statements about published records, not findings of agency fault.</p><p>The held-out predictive coverage is {coverage} ({held['covered']} / {held['eligible']}); directional persistence is {persistence} ({held['persisted']} / {held['flagged']}). Source: outputs/summary.json.</p>{baseline.to_html(index=False,border=0,classes='results').replace('<th>','<th scope="col">')}</section>
<section><h2>Coverage and unresolved data</h2>{inv[['dataset','records','accepted','rejected']].to_html(index=False,border=0).replace('<th>','<th scope="col">')}<p>{html.escape(gaps)}</p></section>
<section><h2>Reproduce and inspect</h2><p>With pinned dependencies and the excluded local frozen inputs at their manifest paths, run <code>make rebuild</code>. All calculations use frozen inputs without network access. The aggregate checkout alone cannot replay the incident audits.</p><ul><li><a href="../TECHNICAL_NOTE.pdf">Technical note (PDF)</a></li><li><a href="../TECHNICAL_NOTE.md">Technical note (Markdown)</a></li></ul><p>Source checksums, catalogues without cached values, aggregate quality tables, monthly provenance and intervals are included. No remote analytics, scripts, fonts or tracking are used on this page.</p></section></main><footer><p>Code: Apache-2.0. Data attribution: Federal Transit Administration, U.S. Department of Transportation, National Transit Database. No endorsement is implied.</p></footer></body></html>
"""
    (root / "docs/site/index.html").write_text(body)
