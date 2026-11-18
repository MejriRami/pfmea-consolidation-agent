"""
Layer 1 — deterministic AP lookup.

AP is never inferred by an AI or recomputed from a formula. It is always a
table lookup against `21_AP_LOOKUP`, extracted verbatim to
ground_truth/ap_lookup.json by Phase 0. This module must never contain the
AIAG-VDA severity/occurrence/detection -> AP logic itself — only the lookup.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DEFAULT_AP_LOOKUP_PATH = Path(__file__).resolve().parent.parent / "ground_truth" / "ap_lookup.json"


class APLookupError(KeyError):
    """Raised when an S/O/D triplet has no entry in the AP lookup table."""


@lru_cache(maxsize=None)
def _load_table(path: str) -> dict[tuple[int, int, int], str]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    table: dict[tuple[int, int, int], str] = {}
    for key, ap in raw.items():
        s, o, d = (int(part) for part in key.split("-"))
        table[(s, o, d)] = str(ap).strip()
    return table


def get_ap(severity: int, occurrence: int, detection: int,
           *, path: str | Path = DEFAULT_AP_LOOKUP_PATH) -> str:
    """Deterministic S/O/D -> AP lookup. Table only, never inferred.

    Raises APLookupError if the triplet is not present in the table.
    """
    table = _load_table(str(path))
    try:
        return table[(int(severity), int(occurrence), int(detection))]
    except KeyError as exc:
        raise APLookupError(
            f"no AP lookup entry for S={severity} O={occurrence} D={detection}"
        ) from exc


def table_size(*, path: str | Path = DEFAULT_AP_LOOKUP_PATH) -> int:
    return len(_load_table(str(path)))
