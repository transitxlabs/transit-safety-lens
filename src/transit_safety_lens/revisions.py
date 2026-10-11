"""Audit differences only between distinct, dated, frozen publication releases."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .quality import KEY, normalise, revision_diff


def analyse_revisions(root: Path, manifest: dict[str, Any], sizes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    register = root / "data/release_pairs.json"
    pairs = json.loads(register.read_text())["pairs"] if register.exists() else []
    entries = {entry["id"]: entry for entry in manifest["sources"]}
    changes, rates = [], []
    for pair in pairs:
        old_entry, new_entry = entries[pair["old_source"]], entries[pair["new_source"]]
        if datetime.fromisoformat(old_entry["release_date"]) >= datetime.fromisoformat(new_entry["release_date"]):
            raise ValueError("Revision pairs must have distinct, increasing publication dates")
        old = normalise(pd.read_csv(root / old_entry["path"], dtype=str), pair["old_source"])
        new = normalise(pd.read_csv(root / new_entry["path"], dtype=str), pair["new_source"])
        columns = pair["columns"]
        for frame in [old, new]:
            for column in columns:
                frame[column] = pd.to_numeric(frame[column], errors="raise")
        difference = revision_diff(old, new, columns)
        difference["pair"] = pair["id"]
        difference["old_release"] = old_entry["release_date"]
        difference["new_release"] = new_entry["release_date"]
        changes.append(difference)
        joined = old[KEY].merge(new[KEY], on=KEY, how="outer", indicator=True, validate="one_to_one")
        changed_keys = difference.loc[difference["record_status"].eq("both"), KEY].drop_duplicates()
        changed_keys["revised"] = True
        joined = joined.merge(changed_keys, on=KEY, how="left", validate="one_to_one")
        joined["revised"] = joined["revised"].eq(True)
        joined = joined.merge(sizes[["ntd_id", "size_band"]], on="ntd_id", how="left", validate="many_to_one")
        joined["size_band"] = joined["size_band"].fillna("unknown")
        joined["year"] = pd.to_datetime(joined["date"]).dt.year
        for dimension, keys in [("all", []), ("year", ["year"]), ("mode", ["mode"]), ("agency_size", ["size_band"])]:
            groups = [("all", joined)] if not keys else joined.groupby(keys, dropna=False)
            for key, group in groups:
                common = group["_merge"].eq("both")
                denominator = int(common.sum())
                revised = int((group["revised"] & common).sum())
                rates.append({"pair": pair["id"], "dimension": dimension, "stratum": str(key),
                              "shared_records": denominator, "revised_records": revised,
                              "revision_rate": revised / denominator if denominator else None,
                              "added_records": int(group["_merge"].eq("right_only").sum()),
                              "removed_records": int(group["_merge"].eq("left_only").sum())})
    return (pd.concat(changes, ignore_index=True) if changes else pd.DataFrame(columns=KEY + ["field", "old_value", "new_value", "record_status", "pair"]),
            pd.DataFrame(rates, columns=["pair", "dimension", "stratum", "shared_records", "revised_records", "revision_rate", "added_records", "removed_records"]))
