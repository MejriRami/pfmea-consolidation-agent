"""
Layer 1 — the closed theme vocabulary.

Frozen at 42 themes by Phase 0 (§3.4 of the spec / decision #3 in
PHASE_0_RESULTS.md): 40 clean themes from the master plus
`material_magnetic_property` and `material_compliance`, added because no
existing clean theme covered those failure modes.

THEMER (an AI, Phase 3) selects from this set only. It never invents a
theme — no match means UNCLASSIFIED, routed to the human queue.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_VOCABULARY_PATH = Path(__file__).resolve().parent.parent / "ground_truth" / "vocabulary.json"

UNCLASSIFIED = "UNCLASSIFIED"


def load_vocabulary(*, path: str | Path = DEFAULT_VOCABULARY_PATH) -> frozenset[str]:
    return frozenset(json.loads(Path(path).read_text(encoding="utf-8")))


THEME_VOCABULARY = load_vocabulary()
