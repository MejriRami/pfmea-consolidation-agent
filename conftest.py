"""Test configuration.

- Ensures the repo root (and therefore the `rules` package) is importable
  regardless of which directory pytest is invoked from.
- Pilot-dataset regression tests: a group of tests replays the agent
  against the confidential pilot answer key (ground_truth/final_lines.jsonl,
  raw_lines.jsonl, decisions.jsonl -- 729 source rows -> 202 validated rows).
  That data belongs to a customer and is not published, so these tests are
  skipped automatically when the files are absent. Drop a ground-truth set
  extracted with `extract_ground_truth.py` into ground_truth/ to run them.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PILOT_DATASET = ROOT / "ground_truth" / "final_lines.jsonl"
HAS_PILOT_DATASET = PILOT_DATASET.exists()

# Modules that load the pilot answer key at import time.
PILOT_DATASET_MODULES = [
    "tests/test_decision_ingestion.py",
    "tests/test_export_final_pfmea.py",
    "tests/test_export_workbook.py",
]

# Individual tests asserting facts about the pilot dataset itself.
PILOT_DATASET_TESTS = {
    "test_build_dedup_key_reproduces_all_192_final_rows",
    "test_partitions_cover_all_192_final_rows",
    "test_no_dedup_key_rows_are_the_two_documented_phase0_orphans",
    "test_desync_defect_is_present_and_material_at_default_threshold",
    "test_aligned_rows_independently_recompute_above_threshold",
    "test_higher_threshold_flags_more_rows_as_desynced",
    "test_resolves_every_non_pfc_row_in_the_actual_ground_truth",
    "test_all_192_ground_truth_rows_have_themes_within_vocabulary_or_unclassified",
}

collect_ignore = [] if HAS_PILOT_DATASET else PILOT_DATASET_MODULES


def pytest_collection_modifyitems(config, items):
    if HAS_PILOT_DATASET:
        return
    skip = pytest.mark.skip(reason="needs the confidential pilot dataset (ground_truth/*.jsonl), not published")
    for item in items:
        if item.originalname in PILOT_DATASET_TESTS or item.name in PILOT_DATASET_TESTS:
            item.add_marker(skip)
