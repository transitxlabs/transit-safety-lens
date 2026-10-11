"""Offline, deterministic rebuild from complete frozen official inputs."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .definitions import assert_comparable, load_timeline, regime
from .distribution import LOCAL_OUTPUTS, assert_public_columns, catalogue_metadata
from .model import Prior, compare, fit_prior, predictive_interval, select_flags
from .publish import render_release
from .quality import KEY, check_records, field_completeness, normalise, rule_rates, size_bins
from .revisions import analyse_revisions


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_csv(path: Path, frame: pd.DataFrame) -> None:
    if path.name not in LOCAL_OUTPUTS:
        assert_public_columns(frame.columns.tolist())
    if path.name in {"monthly_provenance.csv", "field_completeness.csv"}:
        path = path.with_suffix(".csv.gz")
        with path.open("wb") as stream, gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as packed:
            with io.TextIOWrapper(packed, encoding="utf-8", newline="") as text_stream:
                frame.to_csv(text_stream, index=False, float_format="%.10g", lineterminator="\n")
    else:
        frame.to_csv(path, index=False, float_format="%.10g", lineterminator="\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_inputs(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "data/manifest.json").read_text())
    for source in manifest["sources"]:
        path = root / source["path"]
        if not path.is_file() or path.stat().st_size != source["bytes"] or sha256(path) != source["sha256"]:
            raise ValueError(f"Frozen input failed verification: {source['id']}")
    entries = {source["id"]: source for source in manifest["sources"]}
    for source in manifest["sources"]:
        if not source.get("source_parts"):
            continue
        records = []
        for part_id in source["source_parts"]:
            part = entries[part_id]
            with gzip.open(root / part["path"], "rt", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream, strict=True))
            if len(rows) != part["record_count"]:
                raise ValueError("Frozen source page count mismatch")
            records.extend(rows)
        if len(records) != source["record_count"] or len({row["source_row_id"] for row in records}) != len(records):
            raise ValueError("Paged source identities are incomplete or duplicated")
        combined = io.StringIO(newline="")
        writer = csv.DictWriter(combined, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
        if hashlib.sha256(combined.getvalue().encode()).hexdigest() != source["uncompressed_sha256"]:
            raise ValueError("Convenience export differs from its frozen original pages")
    return manifest


def read_dataset(root: Path, manifest: dict[str, Any], dataset: str) -> pd.DataFrame:
    entry = next(s for s in manifest["sources"] if s["id"] == f"{dataset}.csv")
    frame = pd.read_csv(root / entry["path"], dtype=str, keep_default_na=True)
    if len(frame) != entry["record_count"]:
        raise ValueError(f"Record-count mismatch: {dataset}")
    return normalise(frame, dataset)


def annual_panel(monthly: pd.DataFrame, timeline: list[dict[str, Any]]) -> pd.DataFrame:
    """Aggregate TOS only when every observed constituent has all twelve usable months.

    Missing mode-years are never inferred as zero. Constituent entry/exit and missing
    expected months are recorded, and exclude that agency-mode-year from inference.
    """
    rows = []
    for (agency_id, mode, year), group in monthly.groupby(["ntd_id", "mode", "year"], sort=True):
        reasons = []
        for _, service in group.groupby("tos", dropna=False):
            if set(pd.to_datetime(service["date"]).dt.month) != set(range(1, 13)):
                reasons.append("incomplete_months")
            if service["rejected"].any():
                reasons.append("rejected_record")
            if service[["total_events", "vehicle_revenue_miles"]].isna().any().any():
                reasons.append("missing_count_or_exposure")
            if service["vehicle_revenue_miles"].le(0).any():
                reasons.append("nonpositive_exposure")
        try:
            definition = assert_comparable(group["date"].tolist(), timeline)
        except ValueError:
            reasons.append("definition_break")
            definition = "mixed"
        if int(float(str(year))) > 2025:
            reasons.append("unfinished_calendar_year")
        rows.append(dict(ntd_id=agency_id, mode=mode, year=int(float(str(year))),
                         agency=group["agency"].iloc[0], size_band=group["size_band"].iloc[0],
                         definition=definition, months=int(group["date"].nunique()),
                         service_types="|".join(sorted(group["tos"].dropna().unique())),
                         events=group["total_events"].sum(min_count=len(group)),
                         miles=group["vehicle_revenue_miles"].sum(min_count=len(group)),
                         hours=group["vehicle_revenue_hours"].sum(min_count=len(group)),
                         trips=group["ridership"].sum(min_count=len(group)),
                         eligible=not reasons, exclusions="|".join(sorted(set(reasons))),
                         source_records="|".join(group["source_record"])))
    return pd.DataFrame(rows)


def train_priors(annual: pd.DataFrame) -> tuple[dict[tuple[str, str], Prior], pd.DataFrame]:
    # Train after the 2019 service-code transition and before either comparison year.
    train = annual.loc[annual["eligible"] & annual["year"].eq(2019) & annual["size_band"].ne("unknown")].copy()
    priors = {}
    rows = []
    for (mode, band), group in train.groupby(["mode", "size_band"]):
        prior = fit_prior(group["events"].to_numpy(float), group["miles"].to_numpy(float) / 1e6)
        priors[(str(mode), str(band))] = prior
        rows.append(dict(mode=mode, size_band=band, shape=prior.shape, rate=prior.rate,
                         training_groups=prior.groups, status=prior.status, training_years="2019",
                         source_records="|".join(group["source_records"])))
    return priors, pd.DataFrame(rows)


def compare_years(annual: pd.DataFrame, priors: dict[tuple[str, str], Prior],
                  year0: int, year1: int, timeline: list[dict[str, Any]],
                  universe: pd.DataFrame | None = None) -> pd.DataFrame:
    before = annual.loc[annual["year"].eq(year0)]
    after = annual.loc[annual["year"].eq(year1)]
    joined = before.merge(after, on=["ntd_id", "mode"], how="outer", suffixes=("0", "1"),
                          indicator=True, validate="one_to_one")
    if universe is None:
        universe = annual[["ntd_id", "mode"]].drop_duplicates()
    joined = universe.merge(joined, on=["ntd_id", "mode"], how="left", validate="one_to_one")
    rows = []
    for row in joined.to_dict("records"):
        base: dict[str, Any] = dict(ntd_id=row["ntd_id"], mode=row["mode"],
                                    year0=year0, year1=year1, eligible=False, flag="unavailable")
        reasons = []
        if row["_merge"] != "both":
            reasons.append("period_join_loss_or_outside_monthly_safety_coverage")
        else:
            if not row["eligible0"] or not row["eligible1"]:
                reasons.append("incomplete_or_invalid_year")
            if row["service_types0"] != row["service_types1"]:
                reasons.append("service_composition_change")
            try:
                definition = assert_comparable([f"{year0}-01-01", f"{year0}-12-01",
                                                f"{year1}-01-01", f"{year1}-12-01"], timeline)
                base["definition"] = definition
            except ValueError:
                reasons.append("definition_break")
            band = str(row["size_band0"])
            prior = priors.get((str(row["mode"]), band))
            if prior is None:
                reasons.append("no_training_peer_group")
            if not reasons and prior is not None:
                y0, y1 = float(row["events0"]), float(row["events1"])
                e0, e1 = float(row["miles0"]) / 1e6, float(row["miles1"]) / 1e6
                raw0, raw1 = y0 / e0, y1 / e1
                base.update(compare(y0, e0, y1, e1, prior))
                base.update(eligible=True, events0=y0, events1=y1, miles0=e0 * 1e6,
                            miles1=e1 * 1e6, raw_rate0=raw0, raw_rate1=raw1,
                            raw_count_change=y1 - y0, raw_rate_change=raw1 - raw0,
                            raw_count_flag="rate increase" if y1 > y0 else "rate decrease" if y1 < y0 else "same",
                            raw_rate_flag="rate increase" if raw1 > raw0 else "rate decrease" if raw1 < raw0 else "same",
                            size_band=band, agency=row["agency1"],
                            source_records0=row["source_records0"], source_records1=row["source_records1"],
                            prior_key=f"{row['mode']}|{band}", prior_shape=prior.shape, prior_rate=prior.rate)
        base["exclusions"] = "|".join(reasons)
        rows.append(base)
    result = pd.DataFrame(rows)
    available = result.loc[result["eligible"]]
    if len(available):
        selected = select_flags(available["false_sign_probability"].to_numpy(float),
                                (available["ratio_lower"].gt(1) | available["ratio_upper"].lt(1)).to_numpy())
        directions = np.where(available["p_increase"].gt(0.5), "rate increase", "rate decrease")
        result.loc[available.index, "flag"] = np.where(selected, directions, "no_flag")
    return result


def baseline_table(comparisons: pd.DataFrame) -> pd.DataFrame:
    rows = []
    eligible = comparisons.loc[comparisons["eligible"]]
    for baseline in ["raw_count", "raw_rate"]:
        for direction in ["rate increase", "rate decrease"]:
            naive = eligible[f"{baseline}_flag"].eq(direction)
            credible = eligible["flag"].eq(direction)
            rows.append(dict(baseline=baseline, direction=direction, comparisons=len(eligible),
                             naive_flags=int(naive.sum()), credible_flags=int(credible.sum()),
                             kept=int((naive & credible).sum()), dropped=int((naive & ~credible).sum()),
                             added=int((~naive & credible).sum()),
                             opposite_direction=int((naive & eligible["flag"].isin(["rate decrease", "rate increase"])
                                                     & ~credible).sum())))
    return pd.DataFrame(rows)


def holdout(annual: pd.DataFrame, comparison: pd.DataFrame,
            priors: dict[tuple[str, str], Prior]) -> pd.DataFrame:
    current = annual.loc[annual["year"].eq(2022)].set_index(["ntd_id", "mode"])
    rows = []
    for row in comparison.loc[comparison["eligible"]].to_dict("records"):
        key = (row["ntd_id"], row["mode"])
        result = dict(ntd_id=key[0], mode=key[1], flag=row["flag"], eligible=False)
        if key in current.index and current.loc[key, "eligible"]:
            future = current.loc[key]
            if future["service_types"] == annual.loc[(annual["ntd_id"] == key[0])
                                                     & (annual["mode"] == key[1])
                                                     & annual["year"].eq(2021), "service_types"].iloc[0]:
                prior = priors[(key[1], row["size_band"])]
                e = float(future["miles"]) / 1e6
                low, high = predictive_interval(row["events1"], row["miles1"] / 1e6, e, prior)
                held_rate = float(future["events"]) / e
                direction = "rate increase" if held_rate > row["raw_rate0"] else "rate decrease" if held_rate < row["raw_rate0"] else "same"
                result.update(eligible=True, heldout_year=2022, heldout_events=float(future["events"]),
                              heldout_miles=float(future["miles"]), predictive_lower=low,
                              predictive_upper=high, covered=bool(low <= future["events"] <= high),
                              heldout_direction=direction, persists=direction == row["flag"],
                              source_records=future["source_records"])
        rows.append(result)
    return pd.DataFrame(rows)


def reconcile(source: pd.DataFrame, modal: pd.DataFrame, dataset: str) -> pd.DataFrame:
    joined = source[["source_record"] + KEY].merge(modal[["source_record"] + KEY], on=KEY,
                how="left", suffixes=("", "_modal"), indicator=True, validate="many_to_one")
    joined["dataset"] = dataset
    matched = joined["_merge"].eq("both")
    joined["join_reason"] = "service_month_key_absent"
    joined.loc[~joined["mode"].isin(modal["mode"].unique()), "join_reason"] = "mode_outside_target_coverage"
    joined.loc[~joined["ntd_id"].isin(modal["ntd_id"].unique()), "join_reason"] = "agency_outside_target_coverage"
    dates = pd.to_datetime(joined["date"], errors="coerce")
    target_dates = pd.to_datetime(modal["date"], errors="coerce")
    outside = dates.lt(target_dates.min()) | dates.gt(target_dates.max())
    joined.loc[outside, "join_reason"] = "month_outside_target_coverage"
    joined.loc[joined[KEY].isna().any(axis=1), "join_reason"] = "missing_source_key"
    joined.loc[matched, "join_reason"] = "matched"
    joined["join_status"] = np.where(joined["_merge"].eq("both"), "matched", "no_modal_record")
    return joined.drop(columns="_merge")


def rebuild(root: Path, output: Path) -> dict[str, Any]:
    if shutil.disk_usage("/").free < 25_000_000_000:
        raise RuntimeError("Free space on / is below 25 GB")
    manifest = verify_inputs(root)
    catalogue = root / "data/catalog"
    catalogue.mkdir(parents=True, exist_ok=True)
    for source in manifest["sources"]:
        if source["kind"] == "metadata":
            write_json(catalogue / Path(source["path"]).name,
                       catalogue_metadata(json.loads((root / source["path"]).read_text())))
    timeline = load_timeline(root / "data/definitions/timeline.json")
    modes = set(json.loads((root / "data/definitions/timeline.json").read_text())["mode_codes"])
    output.mkdir(parents=True, exist_ok=True)
    datasets = {}
    failures = []
    rates = []
    inventory = []
    field_rates = []
    modal = read_dataset(root, manifest, "modal")
    sizing_checked, _ = check_records(modal, "modal", modes)
    sizes = size_bins(sizing_checked)
    write_csv(output / "agency_sizes.csv", sizes)
    revision_changes, revision_rates = analyse_revisions(root, manifest, sizes)
    write_csv(output / "revision_changes.csv", revision_changes)
    write_csv(output / "revision_rates.csv", revision_rates)
    for name in ["modal", "major", "nonmajor", "fra_security", "exposure"]:
        if not any(s["id"] == f"{name}.csv" for s in manifest["sources"]):
            continue
        frame = modal if name == "modal" else read_dataset(root, manifest, name)
        frame = frame.merge(sizes, on="ntd_id", how="left", validate="many_to_one")
        frame["size_band"] = frame["size_band"].fillna("unknown")
        source_entry = next(entry for entry in manifest["sources"] if entry["id"] == f"{name}.csv")
        published = pd.read_csv(root / source_entry["path"], dtype=str)
        field_rates.append(field_completeness(published, name, frame[["year", "mode", "size_band"]]))
        frame, issues = check_records(frame, name, modes)
        datasets[name] = frame
        failures.append(issues)
        rates.append(rule_rates(frame, name))
        inventory.append(dict(dataset=name, records=len(frame), accepted=int((~frame["rejected"]).sum()),
                              rejected=int(frame["rejected"].sum()),
                              unique_agencies=int(frame["ntd_id"].nunique()),
                              earliest=str(frame["date"].min()), latest=str(frame["date"].max()),
                              keys="|".join(KEY) if name in {"modal", "exposure"} else
                              "ntd_id|year|incident_number" if name != "nonmajor" else
                              "ntd_id|mode|tos|date|eventtype|location|sftsecfl"))
    write_csv(output / "inventory.csv", pd.DataFrame(inventory))
    write_csv(output / "field_completeness.csv", pd.concat(field_rates, ignore_index=True))
    write_csv(output / "quality_issues.csv", pd.concat(failures, ignore_index=True))
    write_csv(output / "quality_rates.csv", pd.concat(rates, ignore_index=True))
    modal = datasets["modal"]
    # Remove ambiguous target keys from joins rather than multiply source records.
    unique_modal = modal.loc[~modal.duplicated(KEY, keep=False) & ~modal[KEY].isna().any(axis=1)]
    joins = []
    for name, frame in datasets.items():
        if name != "modal":
            joined = reconcile(frame, unique_modal, name)
            write_csv(output / f"{name}_joins.csv", joined)
            joins.append(dict(dataset=name, source_records=len(frame), matched=int(joined["join_status"].eq("matched").sum()),
                              lost=int(joined["join_status"].ne("matched").sum())))
    write_csv(output / "join_summary.csv", pd.DataFrame(joins))
    if "exposure" in datasets:
        target = datasets["exposure"]
        target = target.loc[~target.duplicated(KEY, keep=False) & ~target[KEY].isna().any(axis=1)]
        exposure_joins = []
        for name in ["major", "nonmajor", "fra_security", "modal"]:
            if name not in datasets:
                continue
            linked = reconcile(datasets[name], target, name)
            write_csv(output / f"{name}_exposure_joins.csv", linked)
            exposure_joins.append({"dataset": name, "records": len(linked),
                                   "matched": int(linked["join_status"].eq("matched").sum()),
                                   "lost": int(linked["join_status"].ne("matched").sum())})
        write_csv(output / "exposure_join_summary.csv", pd.DataFrame(exposure_joins))
        columns = ["vehicle_revenue_miles", "vehicle_revenue_hours", "ridership"]
        exposure_check = modal[KEY + ["source_record", "size_band", "year"] + columns].merge(
            target[KEY + ["source_record"] + columns], on=KEY, how="left", suffixes=("_modal", "_exposure"),
            indicator=True, validate="many_to_one")
        for column in columns:
            left, right = exposure_check[f"{column}_modal"], exposure_check[f"{column}_exposure"]
            exposure_check[f"eligible_{column}_disagreement"] = left.notna() & right.notna()
            exposure_check[f"failed_{column}_disagreement"] = left.ne(right) & left.notna() & right.notna()
        write_csv(output / "exposure_reconciliation.csv", exposure_check.drop(columns="_merge"))
        rates.append(rule_rates(exposure_check, "exposure_reconciliation"))
        write_csv(output / "quality_rates.csv", pd.concat(rates, ignore_index=True))
    if "major" in datasets and "nonmajor" in datasets:
        major_counts = datasets["major"].groupby(KEY).size().rename("major_events").reset_index()
        nonmajor_counts = datasets["nonmajor"].groupby(KEY)["total_incidents"].sum(min_count=1).rename("nonmajor_events").reset_index()
        reconciliation = modal[KEY + ["source_record", "total_events"]].merge(major_counts, on=KEY, how="left", validate="many_to_one")
        reconciliation = reconciliation.merge(nonmajor_counts, on=KEY, how="left", validate="many_to_one")
        reconciliation["usable_for_reconciliation"] = ~modal.duplicated(KEY, keep=False).to_numpy()
        reconciliation["detail_sum"] = reconciliation[["major_events", "nonmajor_events"]].fillna(0).sum(axis=1)
        reconciliation["difference"] = (reconciliation["detail_sum"] - reconciliation["total_events"]).where(reconciliation["usable_for_reconciliation"])
        reconciliation["interpretation"] = "publication_reconciliation_diagnostic_not_automatic_source_error"
        write_csv(output / "event_reconciliation.csv", reconciliation)
    write_json(output / "model_config.json", {"training_year": 2019, "main_years": [2021, 2022],
        "heldout_flag_years": [2020, 2021], "heldout_year": 2022, "exposure_unit": "million_vehicle_revenue_miles",
        "interval_level": 0.95, "posterior_false_sign_target": 0.05, "minimum_peer_groups": 5,
        "agency_size_boundaries_miles": [1000000, 10000000], "definition_timeline": "data/definitions/timeline.json"})
    write_csv(output / "definition_timeline.csv", pd.DataFrame(timeline))
    provenance = modal[["source_record"] + KEY + ["year", "size_band", "total_events"]
                       + [c for c in ["vehicle_revenue_miles", "vehicle_revenue_hours", "ridership"] if c in modal]].copy()
    provenance["definition"] = provenance["date"].map(lambda d: regime(str(d), timeline))
    provenance["rules_applied"] = "|".join(c.removeprefix("failed_") for c in modal.columns.astype(str) if c.startswith("failed_"))
    failures_by_record = pd.Series("", index=modal.index, dtype="string")
    for col in modal.columns.astype(str):
        if col.startswith("failed_"):
            mask = modal[col]
            failures_by_record.loc[mask] = failures_by_record.loc[mask] + col.removeprefix("failed_") + "|"
    provenance["rule_failures"] = failures_by_record.str.rstrip("|")
    provenance["source_release"] = next(s["release_date"] for s in manifest["sources"] if s["id"] == "modal.csv")
    provenance["source_sha256"] = next(s["sha256"] for s in manifest["sources"] if s["id"] == "modal.csv")
    provenance["rate_per_million_miles"] = modal["total_events"].where(~modal["rejected"]) / modal["vehicle_revenue_miles"].where(modal["vehicle_revenue_miles"].gt(0)) * 1e6
    write_csv(output / "monthly_provenance.csv", provenance)
    annual = annual_panel(modal, timeline)
    write_csv(output / "annual_panel.csv", annual)
    priors, prior_table = train_priors(annual)
    write_csv(output / "priors.csv", prior_table)
    universe = pd.concat([frame[["ntd_id", "mode"]] for name, frame in datasets.items()
                          if name != "exposure"], ignore_index=True).drop_duplicates().dropna()
    universe = universe.sort_values(["ntd_id", "mode"]).reset_index(drop=True)
    write_csv(output / "agency_mode_universe.csv", universe)
    current = compare_years(annual, priors, 2021, 2022, timeline, universe)
    earlier = compare_years(annual, priors, 2020, 2021, timeline, universe)
    write_csv(output / "credible_change.csv", current)
    write_csv(output / "heldout_training_flags.csv", earlier)
    baselines = baseline_table(current)
    write_csv(output / "baseline_comparison.csv", baselines)
    held = holdout(annual, earlier, priors)
    write_csv(output / "heldout.csv", held)
    eligible_held = held.loc[held["eligible"]]
    selected_held = eligible_held.loc[eligible_held["flag"].isin(["rate increase", "rate decrease"])]
    selected = current.loc[current["flag"].isin(["rate increase", "rate decrease"])]
    # Every definition-blocked later comparison remains visible, not silently omitted.
    excluded_pairs = pd.concat([compare_years(annual, priors, y, y + 1, timeline, universe)
                                for y in [2022, 2023, 2024, 2025]], ignore_index=True)
    write_csv(output / "recent_comparison_exclusions.csv", excluded_pairs)
    summary = dict(status="local_research_release_check_required", full_record_inventory=inventory,
                   model=dict(training_year=2019, comparison_years=[2021, 2022],
                              eligible_comparisons=int(current["eligible"].sum()),
                              unavailable_comparisons=int((~current["eligible"]).sum()),
                              flags=int(len(selected)), rate_decrease=int(current["flag"].eq("rate decrease").sum()),
                              rate_increase=int(current["flag"].eq("rate increase").sum()),
                              posterior_expected_false_sign_fraction=float(selected["false_sign_probability"].mean()) if len(selected) else None,
                              target=0.05, interval_level=0.95, conditional_empirical_hyperparameters=True),
                   heldout=dict(training_comparison_years=[2020, 2021], heldout_year=2022,
                                eligible=int(len(eligible_held)), covered=int(eligible_held["covered"].sum()),
                                coverage=float(eligible_held["covered"].mean()) if len(eligible_held) else None,
                                calibration_status="below_nominal_coverage" if len(eligible_held) and eligible_held["covered"].mean() < 0.95 else "nominal_coverage_not_falsified",
                                flagged=int(len(selected_held)), persisted=int(selected_held["persists"].sum()),
                                persistence=float(selected_held["persists"].mean()) if len(selected_held) else None),
                   baselines=baselines.to_dict("records"), joins=joins,
                   revisions=dict(status="measured_on_frozen_release_pairs" if len(revision_rates) else "unavailable_without_two_published_releases",
                                  compared_pairs=int(revision_rates["pair"].nunique()),
                                  compared_releases=2 * int(revision_rates["pair"].nunique()),
                                  rate=float(revision_rates.loc[revision_rates["dimension"].eq("all"), "revised_records"].sum() / revision_rates.loc[revision_rates["dimension"].eq("all"), "shared_records"].sum()) if len(revision_rates) and revision_rates.loc[revision_rates["dimension"].eq("all"), "shared_records"].sum() else None),
                   late_reporting=dict(status="publication_missingness_proxy_only",
                                       actual_submission_timestamps_available=False))
    write_json(output / "summary.json", summary)
    render_release(root, summary, output)
    documents = ["README.md", "DATA.md", "docs/TECHNICAL_NOTE.md", "docs/TECHNICAL_NOTE.pdf",
                 "docs/site/index.html", "docs/site/style.css"]
    write_json(output / "document_checksums.json", {name: sha256(root / name) for name in documents})
    hashes = {str(p.relative_to(output)): sha256(p) for p in sorted(output.iterdir())
              if p.is_file() and p.name != "checksums.json" and p.name not in LOCAL_OUTPUTS}
    write_json(output / "checksums.json", hashes)
    write_json(output / "local_checksums.json", {p.name: sha256(p) for p in sorted(output.iterdir())
               if p.is_file() and p.name in LOCAL_OUTPUTS and p.name != "local_checksums.json"})
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve() if args.output else root / "outputs"
    summary = rebuild(root, output)
    print(json.dumps(summary["model"], sort_keys=True))


if __name__ == "__main__":
    main()
