"""Record-level checks; null values are never silently replaced by zero."""
from __future__ import annotations

import calendar
from typing import Any

import pandas as pd

KEY = ["ntd_id", "mode", "tos", "date"]
EXPOSURES = ["vehicle_revenue_miles", "vehicle_revenue_hours", "ridership"]


def normalise(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    frame = frame.copy()
    frame["source_record"] = [f"{source}:{i + 1}" for i in range(len(frame))]
    frame = frame.rename(columns={"_5_digit_ntd_id": "ntd_id", "type_of_service": "tos",
                                  "vrm": "vehicle_revenue_miles", "vrh": "vehicle_revenue_hours",
                                  "upt": "ridership"})
    frame["ntd_id"] = frame["ntd_id"].astype("string").str.replace(r"\.0$", "", regex=True).str.zfill(5)
    for col in ["mode", "tos"]:
        frame[col] = frame[col].astype("string").str.strip().str.upper()
    if "incident_date" in frame:
        frame["date"] = pd.to_datetime(frame["incident_date"], errors="coerce").dt.strftime("%Y-%m-01")
    elif "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-01")
    elif "month_year" in frame:
        frame["date"] = pd.to_datetime(frame["month_year"], errors="coerce").dt.strftime("%Y-%m-01")
    else:
        months = {month.lower(): number for number, month in enumerate(calendar.month_name) if month}
        month = frame["month"].astype("string").str.lower().map(months)
        numeric = pd.to_numeric(frame["month"], errors="coerce")
        month = month.fillna(numeric)
        frame["date"] = pd.to_datetime(dict(year=pd.to_numeric(frame["year"], errors="coerce"),
                                             month=month, day=1), errors="coerce").dt.strftime("%Y-%m-01")
    frame["year"] = pd.to_datetime(frame["date"]).dt.year
    return frame


def check_records(frame: pd.DataFrame, dataset: str, modes: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = frame.copy()
    issues: list[pd.DataFrame] = []

    def record(rule: str, mask: pd.Series, eligible: pd.Series, fatal: bool) -> None:
        # Keep eligible denominators separately even if there are no failures.
        frame[f"eligible_{rule}"] = eligible
        frame[f"failed_{rule}"] = mask & eligible
        found = frame.loc[mask & eligible, ["source_record", "ntd_id", "mode", "tos", "date", "year"]].copy()
        found["dataset"], found["rule"], found["reject"] = dataset, rule, fatal
        issues.append(found)

    all_rows = pd.Series(True, index=frame.index)
    missing_key = frame[KEY].isna().any(axis=1) | frame[KEY].astype("string").apply(lambda col: col.eq("")).any(axis=1)
    record("missing_key", missing_key, all_rows, True)
    record("unknown_mode", ~frame["mode"].isin(modes), all_rows, True)
    if dataset in {"modal", "exposure"}:
        duplicate = frame.duplicated(KEY, keep=False)
    elif "incident_number" in frame:
        duplicate = frame.duplicated(["ntd_id", "year", "incident_number"], keep=False)
    else:
        # Non-major records are aggregates by event type and location, not unique months.
        duplicate = frame.duplicated(KEY + ["eventtype", "location", "sftsecfl"], keep=False)
    record("duplicate_key", duplicate, all_rows, True)
    count_columns = [col for col in frame.columns.astype(str) if col.startswith("total_") or col in
                     ["property_damage", "customer", "worker", "other"]]
    for col in count_columns + [c for c in EXPOSURES if c in frame]:
        raw_values = frame[col]
        parsed = pd.to_numeric(raw_values, errors="coerce")
        record(f"malformed_{col}", raw_values.notna() & parsed.isna(), all_rows, True)
        frame[col] = parsed
    numeric = frame[count_columns + [c for c in EXPOSURES if c in frame]]
    record("negative_value", numeric.lt(0).any(axis=1), all_rows, True)
    record("nonfinite_value", numeric.isin([float("inf"), float("-inf")]).any(axis=1), all_rows, True)
    counts = frame[[col for col in count_columns if col != "property_damage"]]
    record("fractional_count", (counts.notna() & counts.mod(1).ne(0)).any(axis=1),
           all_rows, True)
    if "total_events" in frame:
        record("missing_safety", frame["total_events"].isna(), all_rows, False)
        recent = pd.to_datetime(frame["date"]) > pd.to_datetime(frame["date"]).max() - pd.DateOffset(months=3)
        record("recent_validation_gap", frame["total_events"].isna(), recent, False)
        components = ["total_collisions", "total_derailments", "total_fires",
                      "total_security_events", "total_events_not_otherwise"]
        if all(col in frame for col in components):
            eligible = frame[components + ["total_events"]].notna().all(axis=1)
            record("event_total_mismatch", frame[components].sum(axis=1).ne(frame["total_events"]),
                   eligible, True)
    if "vehicle_revenue_miles" in frame:
        present = [col for col in EXPOSURES if col in frame]
        record("missing_exposure", frame[present].isna().any(axis=1), all_rows, False)
        known_active = (frame["mode_type_of_service_status"].astype("string").str.lower().isin(["active", "a"])
                        if "mode_type_of_service_status" in frame else all_rows)
        record("possible_late_service", frame[present].isna().any(axis=1), known_active, False)
        record("zero_miles_positive_events", frame["vehicle_revenue_miles"].eq(0)
               & frame.get("total_events", pd.Series(0, index=frame.index)).gt(0),
               frame["vehicle_revenue_miles"].notna(), False)
    frame["rejected"] = False
    for issue in issues:
        frame.loc[frame["source_record"].isin(issue.loc[issue["reject"], "source_record"]), "rejected"] = True
    return frame, pd.concat(issues, ignore_index=True)


def size_bins(frame: pd.DataFrame, train_end: int = 2019) -> pd.DataFrame:
    train = frame.loc[frame["year"].eq(train_end)].copy()
    train["vehicle_revenue_miles"] = pd.to_numeric(train["vehicle_revenue_miles"], errors="coerce")
    rows = []
    for agency_id, group in train.groupby("ntd_id"):
        complete = not group["vehicle_revenue_miles"].isna().any() and not group["rejected"].any()
        for _, service in group.groupby(["mode", "tos"]):
            complete = complete and set(pd.to_datetime(service["date"]).dt.month) == set(range(1, 13))
        if not complete:
            continue
        miles = float(group["vehicle_revenue_miles"].sum())
        band = "under_1m" if miles <= 1_000_000 else "1m_to_10m" if miles <= 10_000_000 else "over_10m"
        rows.append({"ntd_id": agency_id, "size_band": band, "training_year": train_end,
                     "training_annual_miles": miles})
    return pd.DataFrame(rows, columns=["ntd_id", "size_band", "training_year", "training_annual_miles"])


def rule_rates(frame: pd.DataFrame, dataset: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    rules = [col.removeprefix("failed_") for col in frame.columns.astype(str)
             if col.startswith("failed_")]
    flags = [f"{prefix}_{rule}" for rule in rules for prefix in ["eligible", "failed"]]
    for dimension, grouping in [("all", []), ("year", ["year"]), ("mode", ["mode"]),
                                 ("agency_size", ["size_band"]),
                                 ("year_mode_size", ["year", "mode", "size_band"])]:
        aggregate = (frame.groupby(grouping, dropna=False)[flags].sum().reset_index()
                     if grouping else pd.DataFrame([frame[flags].sum()]))
        for values in aggregate.to_dict("records"):
            stratum = "|".join(str(values[col]) for col in grouping) if grouping else "all"
            for rule in rules:
                denominator = int(values[f"eligible_{rule}"])
                failures = int(values[f"failed_{rule}"])
                rows.append({"dataset": dataset, "rule": rule, "dimension": dimension,
                             "stratum": stratum, "failures": failures, "denominator": denominator,
                             "rate": failures / denominator if denominator else None})
    return pd.DataFrame(rows)


def revision_diff(old: pd.DataFrame, new: pd.DataFrame,
                  columns: list[str]) -> pd.DataFrame:
    """Compare genuine releases only; callers must document distinct publication dates."""
    if old.duplicated(KEY).any() or new.duplicated(KEY).any():
        raise ValueError("Revision comparison requires unique record keys")
    joined = old[KEY + columns].merge(new[KEY + columns], on=KEY, how="outer",
                                    suffixes=("_old", "_new"), indicator=True, validate="one_to_one")
    result = []
    for col in columns:
        left, right = joined[f"{col}_old"], joined[f"{col}_new"]
        changed = ~(left.eq(right) | (left.isna() & right.isna())) | joined["_merge"].ne("both")
        subset = joined.loc[changed, KEY].copy()
        subset["field"] = col
        subset["old_value"], subset["new_value"] = left[changed], right[changed]
        subset["record_status"] = joined.loc[changed, "_merge"].astype("string")
        result.append(subset)
    return pd.concat(result, ignore_index=True)


def field_completeness(published: pd.DataFrame, dataset: str,
                       dimensions: pd.DataFrame) -> pd.DataFrame:
    """Availability of all published fields; optional/redacted nulls are not asserted errors."""
    measured = published.columns.astype(str).tolist()
    missing = published.isna().astype(int).copy()
    dimension_names = ["__dimension_year", "__dimension_mode", "__dimension_size"]
    if set(dimension_names).intersection(measured) or len(published) != len(dimensions):
        raise ValueError("Invalid published field/dimension alignment")
    for source, target in zip(["year", "mode", "size_band"], dimension_names, strict=True):
        missing[target] = dimensions[source].to_numpy()
    rows = []
    for dimension, groups in [("all", []), ("year", dimension_names[:1]),
                               ("mode", dimension_names[1:2]), ("agency_size", dimension_names[2:]),
                               ("year_mode_size", dimension_names)]:
        if groups:
            grouped = missing.groupby(groups, dropna=False)
            totals = grouped[measured].sum().copy().reset_index().copy()
            totals["denominator"] = grouped.size().to_numpy()
        else:
            totals = pd.DataFrame([missing[measured].sum()])
            totals["denominator"] = len(published)
        for values in totals.to_dict("records"):
            stratum = "|".join(str(values[key]) for key in groups) if groups else "all"
            for field in measured:
                denominator = int(values["denominator"])
                nulls = int(values[field])
                rows.append({"dataset": dataset, "field": field, "dimension": dimension,
                             "stratum": stratum, "null_or_empty": nulls, "denominator": denominator,
                             "availability_rate": 1 - nulls / denominator if denominator else None})
    return pd.DataFrame(rows)
