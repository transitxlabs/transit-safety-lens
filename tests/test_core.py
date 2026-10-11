import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from transit_safety_lens.definitions import assert_comparable, load_timeline, regime
from transit_safety_lens.distribution import assert_public_columns, catalogue_metadata
from transit_safety_lens.model import Prior, compare, fit_prior, predictive_interval, select_flags
from transit_safety_lens.pipeline import annual_panel, baseline_table, reconcile, verify_inputs
from transit_safety_lens.quality import check_records, normalise, revision_diff, rule_rates

ROOT = Path(__file__).resolve().parents[1]


def test_fixture_quality_preserves_missing_values_and_rejects_ambiguous_keys():
    data = normalise(pd.read_csv(ROOT / "tests/fixtures/modal.csv", dtype=str), "fixture")
    checked, issues = check_records(data, "modal", {"MB"})
    checked["size_band"] = "unknown"
    assert len(checked) == 6
    assert checked["rejected"].sum() == 4
    assert pd.isna(checked.loc[1, "total_events"])
    assert pd.isna(checked.loc[1, "vehicle_revenue_miles"])
    assert set(issues["rule"]) >= {"missing_safety", "negative_value", "duplicate_key", "unknown_mode"}
    rates = rule_rates(checked, "modal")
    missing = rates.loc[(rates["rule"] == "missing_safety") & (rates["dimension"] == "all")].iloc[0]
    assert missing["failures"] == 1 and missing["denominator"] == 6


def test_definition_guard_blocks_changed_total_but_allows_unaffected_person_metric():
    timeline = load_timeline(ROOT / "data/definitions/timeline.json")
    with pytest.raises(ValueError, match="definition"):
        assert_comparable(["2022-12-01", "2023-01-01"], timeline)
    with pytest.raises(ValueError, match="definition"):
        assert_comparable(["2018-12-01", "2019-01-01"], timeline)
    assert assert_comparable(["2021-01-01", "2022-12-01"], timeline)
    assert regime("2015-01-01", timeline, "suicide_injuries") != regime("2016-01-01", timeline, "suicide_injuries")


def test_gamma_ratio_matches_draws_and_swaps_symmetrically():
    prior = Prior(0.5, 0.2, 10, "fixture")
    result = compare(5, 2, 15, 3, prior)
    swapped = compare(15, 3, 5, 2, prior)
    assert result["ratio_median"] * swapped["ratio_median"] == pytest.approx(1)
    assert result["p_increase"] + swapped["p_increase"] == pytest.approx(1)
    rng = np.random.default_rng(410)
    draws = rng.gamma(15.5, 1 / 3.2, 200_000) / rng.gamma(5.5, 1 / 2.2, 200_000)
    assert np.mean(draws > 1) == pytest.approx(result["p_increase"], abs=0.004)
    assert np.quantile(draws, 0.025) == pytest.approx(result["ratio_lower"], rel=0.02)


def test_error_control_and_interval_gate():
    errors = np.array([0.001, 0.02, 0.4, 0.03])
    selected = select_flags(errors, np.array([True, True, False, False]), q=0.05)
    assert selected.tolist() == [True, True, False, False]
    assert errors[selected].mean() <= 0.05
    assert not select_flags(np.array([]), np.array([], dtype=bool)).any()
    with pytest.raises(ValueError):
        select_flags(np.array([np.nan]), np.array([True]))


def test_pooled_prior_and_predictive_calibration_under_the_stated_model():
    rng = np.random.default_rng(105)
    e = rng.uniform(0.5, 10, 1500)
    rates = rng.gamma(2, 1 / 0.4, 1500)
    y = rng.poisson(e * rates)
    prior = fit_prior(y, e)
    assert prior.shape == pytest.approx(2, rel=0.2)
    assert prior.rate == pytest.approx(0.4, rel=0.2)
    future = rng.poisson(e * rates)
    covered = [low <= z <= high for count, exposure, z in zip(y, e, future, strict=True)
               for low, high in [predictive_interval(count, exposure, exposure, prior)]]
    assert 0.93 < np.mean(covered) < 0.985


def test_incomplete_year_is_never_completed_with_zeros():
    frame = normalise(pd.read_csv(ROOT / "tests/fixtures/modal.csv", dtype=str), "fixture")
    frame, _ = check_records(frame, "modal", {"MB"})
    frame["size_band"] = "under_1m"
    panel = annual_panel(frame, load_timeline(ROOT / "data/definitions/timeline.json"))
    assert not panel["eligible"].any()
    assert panel["exclusions"].str.contains("incomplete_months").all()


def test_join_loss_is_counted_without_expanding_events():
    modal = pd.DataFrame({"source_record": ["m:1"], "ntd_id": ["00001"], "mode": ["MB"],
                          "tos": ["DO"], "date": ["2021-01-01"]})
    source = pd.concat([modal.assign(source_record="e:1"), modal.assign(source_record="e:2"),
                        modal.assign(source_record="e:3", ntd_id="99999")], ignore_index=True)
    joined = reconcile(source, modal, "major")
    assert len(joined) == 3
    assert joined["join_status"].eq("matched").sum() == 2
    assert joined["join_status"].eq("no_modal_record").sum() == 1
    with pytest.raises(pd.errors.MergeError):
        reconcile(source, pd.concat([modal, modal]), "major")


def test_revision_does_not_count_missing_to_missing_as_changed():
    old = pd.DataFrame({"ntd_id": ["00001", "00002"], "mode": ["MB", "MB"], "tos": ["DO", "DO"],
                        "date": ["2021-01-01", "2021-01-01"], "total_events": [np.nan, 2]})
    new = old.copy()
    new.loc[1, "total_events"] = 3
    diff = revision_diff(old, new, ["total_events"])
    assert len(diff) == 1 and diff.iloc[0]["ntd_id"] == "00002"
    with pytest.raises(ValueError):
        revision_diff(pd.concat([old, old]), new, ["total_events"])


def test_input_checksum_failure(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/file.csv").write_text("x\n1\n")
    (tmp_path / "data/manifest.json").write_text(json.dumps({"sources": [
        {"id": "file", "path": "data/file.csv", "bytes": 4, "sha256": "invalid"}]}))
    with pytest.raises(ValueError, match="verification"):
        verify_inputs(tmp_path)


def test_baseline_accounting():
    frame = pd.DataFrame({"eligible": [True, True, True, False],
                          "raw_count_flag": ["rate increase", "rate decrease", "same", "same"],
                          "raw_rate_flag": ["rate decrease", "rate increase", "same", "same"],
                          "flag": ["rate increase", "no_flag", "rate decrease", "unavailable"]})
    table = baseline_table(frame)
    for row in table.to_dict("records"):
        assert row["naive_flags"] == row["kept"] + row["dropped"]
        assert row["credible_flags"] == row["kept"] + row["added"]


def test_exposure_can_reverse_the_naive_count_direction():
    result = compare(100, 1, 150, 10, Prior(0.5, 0.01, 20, "fixture"))
    assert 150 > 100
    assert result["ratio_upper"] < 1
    assert result["p_increase"] < 0.001


def test_missing_and_malformed_keys_are_quarantined():
    frame = normalise(pd.read_csv(ROOT / "tests/fixtures/modal.csv", dtype=str), "fixture")
    frame.loc[0, "date"] = None
    frame.loc[1, "total_events"] = "not_a_count"
    checked, issues = check_records(frame, "modal", {"MB"})
    assert checked.loc[0, "rejected"] and checked.loc[1, "rejected"]
    assert set(issues["rule"]) >= {"missing_key", "malformed_total_events"}


def test_future_outcomes_cannot_change_training_priors():
    from transit_safety_lens.pipeline import train_priors
    annual = pd.DataFrame({"ntd_id": ["00001"] * 3, "mode": ["MB"] * 3,
                           "year": [2019, 2021, 2022], "eligible": [True] * 3,
                           "size_band": ["under_1m"] * 3, "events": [10, 20, 30],
                           "miles": [1_000_000] * 3, "source_records": ["m:1", "m:2", "m:3"]})
    before, _ = train_priors(annual)
    annual.loc[annual["year"].gt(2019), "events"] = 10_000_000
    after, _ = train_priors(annual)
    assert before == after


def test_revision_register_requires_distinct_release_dates(tmp_path):
    from transit_safety_lens.revisions import analyse_revisions
    (tmp_path / "data").mkdir()
    (tmp_path / "data/release_pairs.json").write_text(json.dumps({"pairs": [
        {"id": "same", "old_source": "a", "new_source": "b", "columns": ["total_events"]}]}))
    manifest = {"sources": [{"id": "a", "release_date": "2026-10-01T00:00:00+00:00"},
                            {"id": "b", "release_date": "2026-10-01T00:00:00+00:00"}]}
    with pytest.raises(ValueError, match="publication dates"):
        analyse_revisions(tmp_path, manifest, pd.DataFrame())


def test_all_published_field_availability_includes_dimension_fields():
    from transit_safety_lens.quality import field_completeness
    frame = pd.DataFrame({"year": [2021, None], "mode": ["MB", None], "optional": [None, None]})
    dimensions = pd.DataFrame({"year": [2021, 2021], "mode": ["MB", "MB"], "size_band": ["unknown"] * 2})
    result = field_completeness(frame, "fixture", dimensions)
    all_rows = result.loc[result["dimension"].eq("all")].set_index("field")
    assert set(all_rows.index) == set(frame.columns)
    assert all_rows.loc["year", "null_or_empty"] == 1
    assert all_rows.loc["mode", "availability_rate"] == 0.5
    assert all_rows.loc["optional", "availability_rate"] == 0


def test_catalogue_removes_nested_samples_without_losing_schema_or_terms():
    original = {"licenseId": "USGOV_WORKS", "columns": [
        {"fieldName": "incident_description", "description": "Published source field",
         "cachedContents": {"top": [{"item": "private incident detail"}]}},
    ], "metadata": {"nested": {"cachedContents": {"largest": "private location"}}}}
    published = catalogue_metadata(original)
    assert published["licenseId"] == "USGOV_WORKS"
    assert published["columns"][0]["fieldName"] == "incident_description"
    assert "private incident detail" not in json.dumps(published)
    assert "private location" not in json.dumps(published)
    assert "cachedContents" in original["columns"][0]


@pytest.mark.parametrize("column", ["incident_description", "assault_homicide_person_type_desc",
                                    "approximate_address", "latitude", "longitude", "person_list",
                                    "additional_assault_information"])
def test_release_writer_rejects_private_row_columns(tmp_path, column):
    from transit_safety_lens.pipeline import write_csv
    with pytest.raises(ValueError, match="Private source fields"):
        write_csv(tmp_path / "aggregate.csv", pd.DataFrame({column: ["private detail"]}))
    assert not (tmp_path / "aggregate.csv").exists()
    assert_public_columns(["dataset", "field", "null_or_empty", "denominator"])
