"""Tests for the isolated real-data ingest track (pipeline.data.ingest).

These verify the mapping / validation / manifest / sha256 pipeline with a small
German-Credit-like fixture. They do NOT touch the production demo model.
"""
from __future__ import annotations

import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from pipeline.data.ingest import ingest, sha256_file

ROOT = Path(__file__).resolve().parents[1]
MAPPING = ROOT / "pipeline" / "data" / "mapping.yaml"


def _write_raw(tmp: Path) -> Path:
    raw = tmp / "raw" / "german_credit.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        {
            "age": [32, 45, 60, 28],
            "credit_amount": [8000, 12000, 2000, 5000],
            "duration": [24, 36, 12, 48],
            "checking_status": ["no checking", "0", "1", "2"],
            "email": ["a@x.com", "b@y.com", "c@z.com", "d@w.com"],
            "class": [1, 2, 1, 2],
        }
    )
    df.to_csv(raw, index=False)
    return raw


def test_ingest_maps_target_and_emits_manifest(tmp_path):
    raw = _write_raw(tmp_path)
    out = tmp_path / "processed" / "creditflow_real.csv"
    meta = tmp_path / "metadata" / "dataset_manifest.json"

    manifest = ingest(raw, MAPPING, out, meta, source_name="german_credit_fixture")

    # Processed file + manifest written.
    assert out.exists(), "processed CSV should be written"
    assert meta.exists(), "manifest should be written"
    assert manifest["ready_for_training"] is False  # missing canonical features (by design)

    # Target normalization: class 1 -> 0 (good), class 2 -> 1 (default risk).
    df = pd.read_csv(out)
    assert "default" in df.columns
    assert df["default"].tolist() == [0, 1, 0, 1]

    # Provenance.
    assert manifest["sha256"] == sha256_file(out)
    assert manifest["rows"] == 4
    assert manifest["dataset"] == "german_credit"
    assert "income" in manifest["missing_canonical_columns"]
    # Unmapped source columns are either listed as dropped or surfaced as unmapped.
    assert "email" not in df.columns
    assert ("email" in manifest["unmapped_raw_columns"]) or any("email" in c for c in manifest["dropped_columns"])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        test_ingest_maps_target_and_emits_manifest(Path(d))
        print("  PASS: test_ingest_maps_target_and_emits_manifest")
    print("\nAll ingest tests passed.")