#!/usr/bin/env python3
"""Replay a committed checkout without network and verify every output checksum."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--local-inputs", type=Path, required=True,
                        help="Local project tree holding excluded frozen inputs at manifest paths")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = args.destination.resolve()
    if destination.exists():
        raise FileExistsError("Replay destination must be new")
    destination.mkdir(parents=True)
    archive = destination / "checkout.tar"
    with archive.open("wb") as stream:
        subprocess.run(["git", "archive", "HEAD"], cwd=root, stdout=stream, check=True)
    checkout = destination / "checkout"
    checkout.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(checkout, filter="data")
    # Only excluded inputs are staged; the code and release outputs come from the archive.
    manifest = json.loads((checkout / "data/manifest.json").read_text())
    local_root = args.local_inputs.resolve()
    staged = 0
    for source in manifest["sources"]:
        if source.get("distribution") != "local_only":
            continue
        relative = Path(source["path"])
        original = (local_root / relative).resolve()
        target = (checkout / relative).resolve()
        if not original.is_relative_to(local_root) or not target.is_relative_to(checkout):
            raise ValueError("Frozen input path escapes its project tree")
        if (not original.is_file() or original.stat().st_size != source["bytes"]
                or hashlib.sha256(original.read_bytes()).hexdigest() != source["sha256"]):
            raise ValueError(f"Local frozen input failed verification: {source['id']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        staged += 1
    expected = json.loads((checkout / "outputs/checksums.json").read_text())
    command = [sys.executable, "-m", "transit_safety_lens.pipeline", "--root", str(checkout)]
    # Launch with only the checkout's package on its search path, using preinstalled dependencies.
    runner = ("import runpy,socket,sys; "
              "socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('Network forbidden')); "
              f"sys.path.insert(0,{str(checkout / 'src')!r}); "
              f"sys.argv={command[2:]!r}; "
              "runpy.run_module('transit_safety_lens.pipeline',run_name='__main__')")
    subprocess.run([sys.executable, "-c", runner], cwd=checkout, check=True)
    observed = json.loads((checkout / "outputs/checksums.json").read_text())
    if expected != observed:
        mismatch = sorted(name for name in set(expected) | set(observed)
                          if expected.get(name) != observed.get(name))
        raise ValueError(f"Replay output mismatch: {mismatch}")
    local_expected = json.loads((root / "outputs/local_checksums.json").read_text())
    local_observed = json.loads((checkout / "outputs/local_checksums.json").read_text())
    if local_expected != local_observed:
        raise ValueError("Local diagnostic replay checksum mismatch")
    receipt = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
               "network": "disabled_in_process", "outputs_compared": len(expected),
               "local_inputs_staged": staged, "local_outputs_compared": len(local_expected),
               "local_checksums_match": True,
               "checksums_match": True, "destination": str(checkout), "exit_code": 0}
    (destination / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
