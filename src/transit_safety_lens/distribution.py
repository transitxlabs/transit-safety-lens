"""Separate frozen local evidence from the aggregate release package."""
from __future__ import annotations

from typing import Any

LOCAL_OUTPUTS = {
    "major_joins.csv", "major_exposure_joins.csv",
    "fra_security_joins.csv", "fra_security_exposure_joins.csv",
    "nonmajor_joins.csv", "nonmajor_exposure_joins.csv",
    "quality_issues.csv", "revision_changes.csv", "local_checksums.json",
}

# These are source schema names, never publishable row-valued output columns.
PRIVATE_COLUMNS = {
    "incident_description", "incident_date", "incident_time", "incident_number",
    "assault_homicide_person_type_desc", "assault_homicide_type_desc",
    "assault_homicide_transit_worker_flag", "other_event_type_description",
    "approximate_address", "latitude", "longitude", "latlon", "person_list",
    "multiple_locations_list", "evacuation_comment", "evacuation_location",
    "hazmat_type_description", "other_fire_fuel_description", "weather_comment",
    "rail_grade_crossing_comment", "revenue_vehicle_identifier_list",
    "manufacturer_description", "additional_assault_information",
}


def local_source(source: dict[str, Any]) -> bool:
    return source.get("distribution") == "local_only"


def catalogue_metadata(value: Any) -> Any:
    """Keep catalogue descriptions and schema, omit cached source values and identities."""
    if isinstance(value, dict):
        return {key: catalogue_metadata(child) for key, child in value.items()
                if key not in {"cachedContents", "owner", "tableAuthor", "rowsUpdatedBy",
                               "approvals", "grants", "clientContext"}}
    if isinstance(value, list):
        return [catalogue_metadata(child) for child in value]
    return value


def assert_public_columns(columns: list[str]) -> None:
    if set(columns) & PRIVATE_COLUMNS:
        raise ValueError("Private source fields cannot be released as row-valued columns")
