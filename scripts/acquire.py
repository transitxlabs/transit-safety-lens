#!/usr/bin/env python3
"""Acquire full official exports with count reconciliation and immutable release manifests.

This command is separate from the offline rebuild. It requires network access only
on data.transportation.gov and approved FTA data/document URLs.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import shutil
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ALLOWED = {"data.transportation.gov", "www.transit.dot.gov", "transit.dot.gov"}
DATASETS = {"modal": "5ti2-5uiv", "major": "9ivb-8ae9", "nonmajor": "urir-txqm",
            "fra_security": "65fa-qbkf", "exposure": "8bui-9xvu"}


def request(url: str) -> bytes:
    if urllib.parse.urlparse(url).hostname not in ALLOWED:
        raise ValueError("Source outside official allowlist")
    if shutil.disk_usage("/").free < 25_000_000_000:
        raise RuntimeError("Free space below 25 GB")
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=90) as response:
        if urllib.parse.urlparse(response.url).hostname not in ALLOWED:
            raise ValueError("Redirect outside official allowlist")
        content = response.read()
        return gzip.decompress(content) if response.headers.get("Content-Encoding") == "gzip" else content


def acquire(root: Path, destination: Path, names: list[str]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    for name in names:
        dataset_id = DATASETS[name]
        metadata_url = f"https://data.transportation.gov/api/views/{dataset_id}.json"
        metadata_bytes = request(metadata_url)
        metadata = json.loads(metadata_bytes)
        version = datetime.fromtimestamp(metadata["rowsUpdatedAt"], timezone.utc).isoformat()
        query = urllib.parse.urlencode({"$select": "count(*)"})
        count = int(json.loads(request(f"https://data.transportation.gov/resource/{dataset_id}.json?{query}"))[0]["count"])
        query = urllib.parse.urlencode({"$limit": count + 1, "$order": ":id"})
        url = f"https://data.transportation.gov/resource/{dataset_id}.csv?{query}"
        part_ids = []
        if name == "major":
            records = []
            for offset in range(0, count, 10000):
                expected = min(10000, count - offset)
                page_query = urllib.parse.urlencode({"$select": "*, :id as source_row_id",
                    "$limit": expected, "$offset": offset, "$order": ":id"})
                page_url = f"https://data.transportation.gov/resource/{dataset_id}.csv?{page_query}"
                page_bytes = request(page_url)
                page_records = list(csv.DictReader(io.StringIO(page_bytes.decode("utf-8-sig")), strict=True))
                if len(page_records) != expected:
                    raise ValueError("Incomplete bounded export page")
                records.extend(page_records)
                part_id = f"{name}_page_{offset:06d}"
                part_path = destination / f"{part_id}.csv.gz"
                if part_path.exists():
                    raise FileExistsError("Use a new destination")
                packed = gzip.compress(page_bytes, mtime=0)
                part_path.write_bytes(packed)
                entries.append({"id": part_id, "path": str(part_path.relative_to(root)),
                    "url": page_url, "kind": "record_page", "release_date": version,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(), "bytes": len(packed),
                    "sha256": hashlib.sha256(packed).hexdigest(), "record_count": expected,
                    "uncompressed_bytes": len(page_bytes),
                    "uncompressed_sha256": hashlib.sha256(page_bytes).hexdigest(),
                    "terms": metadata.get("licenseId")})
                part_ids.append(part_id)
            if len({record["source_row_id"] for record in records}) != count:
                raise ValueError("Incomplete or repeated public row identities")
            combined = io.StringIO(newline="")
            writer = csv.DictWriter(combined, fieldnames=list(records[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
            content = combined.getvalue().encode()
        else:
            content = request(url)
        actual = sum(1 for _ in csv.DictReader(io.StringIO(content.decode("utf-8-sig")), strict=True))
        if actual != count:
            raise ValueError(f"Incomplete export {name}: {actual} != {count}")
        if json.loads(request(metadata_url))["rowsUpdatedAt"] != metadata["rowsUpdatedAt"]:
            raise ValueError(f"Release changed while acquiring {name}")
        for suffix, value, kind in [("_metadata.json", metadata_bytes, "metadata"),
                                     (".csv.gz", gzip.compress(content, mtime=0), "records")]:
            path = destination / f"{name}{suffix}"
            if path.exists():
                raise FileExistsError("Use a new destination; never overwrite frozen releases")
            path.write_bytes(value)
            entry = dict(id=f"{name}.csv" if kind == "records" else path.name,
                         path=str(path.relative_to(root)), url=url if kind == "records" else metadata_url,
                         kind=kind, release_date=version, bytes=len(value),
                         sha256=hashlib.sha256(value).hexdigest(), terms=metadata.get("licenseId"),
                         retrieved_at=datetime.now(timezone.utc).isoformat())
            if kind == "records":
                entry.update(record_count=count, uncompressed_bytes=len(content),
                             uncompressed_sha256=hashlib.sha256(content).hexdigest())
                if part_ids:
                    entry.update(source_parts=part_ids, acquisition_method="lossless_normalised_concatenation_of_complete_frozen_csv_pages")
            entries.append(entry)
        (destination / "manifest.json").write_text(json.dumps({"sources": entries}, indent=2) + "\n")
        print(name, count, version)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--dataset", choices=list(DATASETS), action="append")
    args = parser.parse_args()
    root = args.root.resolve()
    destination = (root / args.destination).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("Destination must be inside the project")
    if not destination.is_relative_to(root / "work-tmp"):
        raise ValueError("Raw acquisitions must remain in the ignored work-tmp directory")
    acquire(root, destination, args.dataset or list(DATASETS))


if __name__ == "__main__":
    main()
